import asyncio
from aiohttp import web
from aiohttp import ClientSession
import logging
from typing import Any, Optional, cast
from socket import inet_aton, inet_ntoa
from dataclasses import dataclass
from zeroconf import IPVersion, ServiceStateChange, Zeroconf
from zeroconf.asyncio import (
    AsyncServiceBrowser,
    AsyncServiceInfo,
    AsyncZeroconf,
    AsyncZeroconfServiceTypes,
)
import aiofiles
from json import loads, dumps
from io import StringIO
from hashlib import md5
from yarl import URL
import tags, dmap_parser, tag_definitions
import binascii
import time
import random
from textwrap import wrap

# update these
SERVER_NAME = "NotUxPlay"
ADDRESS = "192.168.1.35"
SERVER_ADDRESSES = [inet_aton(ADDRESS),]
SERVER_PORT = 33689
ARROWS_PORT = 34999

UXPLAY_DACP_FILE = "./.uxplay.dacp"
# format:
# line 1: dacp_id (hex, uppercase)
# line 2: active remote (integer)

SUB_TEXT = 1471545639  # int.from_bytes(random.randbytes(4)) # used as an encryption key. // todo: set this per-session??? or should be constant?
DAAP_DATABASE_ID = "DDEA93B661D72B89" # binascii.hexlify(random.randbytes(8)).decode("utf-8").upper() gen these once?
DAAP_SERVER_ID = "793C37358B18AEF8"   #        ^ ^ ^

CMBE_COMMAND_TO_DACP_COMMAND = {
    "playpause": "playpause",
    "menu": None,
    "topmenu": "stop",
    "select": None,
}

ARROWS_TO_DACP_COMMAND = {
    "left": "previtem",
    "right": "nextitem",
    "down": "volumedown",
    "up": "volumeup",
}

# // todo: error handling


@dataclass
class ClientRemotePairingRecord:
    fqn: str
    port: int
    pairing_guid: str
    addresses: list[str]
    name: str

@dataclass
class ClientRemoteControlRecord:
    fqn: str
    port: int
    addresses: list[str]
    name: str

class AsyncRunner:
    def __init__(self, app) -> None:
        self.aiobrowser: Optional[AsyncServiceBrowser] = None
        self.aiozc: Optional[AsyncZeroconf] = None
        self.app: web.Application = app
        self.app[remote_pairing_mdns_entries] = {}
        self.app[remote_control_mdns_entries] = {}

    async def async_run(self) -> None:
        self.aiozc = AsyncZeroconf(ip_version=IPVersion.All)
        self.service_info = AsyncServiceInfo(
            "_touch-able._tcp.local.",
            f"{DAAP_SERVER_ID}._touch-able._tcp.local.",
            addresses=SERVER_ADDRESSES,
            port=SERVER_PORT,
            properties={
                "txtvers": "1",  # format version
                "atSV": "65541", #?
                "DbId": DAAP_DATABASE_ID, # DMAP/DAAP database id
                "CtlN": SERVER_NAME, # Controller Name?
                "DvTy": "AppleTV", # Development Type???
                "DvSv": "1792",    # Development Server???
                "atCV": "65539",   # some sort of prime number for crypto?
                "Ver": "100000",   # version
            },
            server=f"{SERVER_NAME.replace(' ', '-')}.local."
        )

        self.services = ["_touch-remote._tcp.local.", "_dacp._tcp.local."]

        self.aiobrowser = AsyncServiceBrowser(
            self.aiozc.zeroconf, self.services, handlers=[self.async_on_service_state_change]
        )
        await self.aiozc.async_register_service(self.service_info)
        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break

    async def async_close(self) -> None:
        assert self.aiozc is not None
        assert self.aiobrowser is not None
        await self.aiozc.async_unregister_service(self.service_info)
        await self.aiobrowser.async_cancel()
        await self.aiozc.async_close()

    def delete_entry(self, name, service_type):
        if service_type == "_touch-remote._tcp.local.":
            if name in self.app[remote_pairing_mdns_entries]:
                del self.app[remote_pairing_mdns_entries][name]
        elif service_type == "_dacp._tcp.local.":
            if name in self.app[remote_control_mdns_entries]:
                del self.app[remote_control_mdns_entries][name]

    def async_on_service_state_change(self,
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        print(f"Service {name} of type {service_type} state changed: {state_change}")
        if service_type not in self.services: # guard
            return
        if state_change == ServiceStateChange.Removed:
            self.delete_entry(name, service_type)    
        else:
            asyncio.ensure_future(self.async_display_service_info(zeroconf, service_type, name, state_change))


    async def async_display_service_info(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)
        print("Info from zeroconf.get_service_info: %r" % (info))
        if not info:
            print("  No info")
            return
        addresses = [(addr, cast(int, info.port)) for addr in info.parsed_scoped_addresses()]
        
        print("  Name: %s" % name)
        # print("  Addresses: %s" % ", ".join(addresses))

        print(f"  Server: {info.server}")
        if info.properties:
            print("  Properties are:")
            for key, value in info.properties.items():
                print(f"    {key!r}: {value!r}")
        else:
            print("  No properties")
        
        if not info.properties:
            print("No properties, bad record")
            self.delete_entry(name, service_type)
            return
        if service_type == "_touch-remote._tcp.local.":
            if b'Pair' not in info.properties:
                print("No Pair key in properites, bad record")
                self.delete_entry(name, service_type)
                return
            pairing_guid = info.properties[b'Pair'].decode("utf-8")
            pretty_name = info.properties[b'DvNm'].decode("utf-8") if b'DvNm' in info.properties else name.replace("._touch-able._tcp.local.", "")
            record = ClientRemotePairingRecord(name, info.port, pairing_guid, addresses, pretty_name)
            self.app[remote_pairing_mdns_entries][name] = record
        elif service_type == "_dacp._tcp.local.":
            pretty_name = name.replace("._dacp._tcp.local.", "")
            record = ClientRemoteControlRecord(name, info.port, addresses, pretty_name)
            self.app[remote_control_mdns_entries][name] = record
            print(record)

        print('\n')

async def mdns_task(app):
    runner = AsyncRunner(app)
    logging.info("test")
    app[mdns_manager] = asyncio.create_task(runner.async_run())

    yield
    
    app[mdns_manager].cancel()
    await runner.async_close()

class ArrowServerProtocol(asyncio.Protocol):
    def __init__(self, app):
        super()
        self.app = app

    def connection_made(self, transport):
        peername = transport.get_extra_info('peername')
        print('Connection from {}'.format(peername))
        self.transport = transport

    def data_received(self, data):
        message = binascii.hexlify(data)
        print('Data received: {!r}'.format(message))
        start_bytes = data[0:4]
        using_session = None
        for session_id, _session in app[session].items():
            print(f"start_bytes for {session_id}", _session['trackpad_expected_start_bytes'], start_bytes)
            if start_bytes == _session['trackpad_expected_start_bytes']:
                print(f"using session {_session}")
                using_session = _session
                break
        if using_session is None:
            print("could not find session")
            return
        decrypted_message = [using_session['trackpad_key'] ^ int.from_bytes(int(x,16).to_bytes(4, 'big')) for x in wrap(message.decode("utf-8"),8)]
        print("Decrypted Message: ", decrypted_message)
        if decrypted_message[7] == 10486038:
            if "down" in ARROWS_TO_DACP_COMMAND and ARROWS_TO_DACP_COMMAND["down"] is not None:
                asyncio.ensure_future(make_request_to_uxplay_client(using_session, command=ARROWS_TO_DACP_COMMAND["down"]), loop=self.app.loop)
            print("ARROW DOWN")
        elif decrypted_message[7] == 10485938:
            if "up" in ARROWS_TO_DACP_COMMAND and ARROWS_TO_DACP_COMMAND["up"] is not None:
                asyncio.ensure_future(make_request_to_uxplay_client(using_session, command=ARROWS_TO_DACP_COMMAND["up"]), loop=self.app.loop)
            print("ARROW UP")
        elif decrypted_message[7] == 7209188:
            if "left" in ARROWS_TO_DACP_COMMAND and ARROWS_TO_DACP_COMMAND["left"] is not None:
                asyncio.ensure_future(make_request_to_uxplay_client(using_session, command=ARROWS_TO_DACP_COMMAND["left"]), loop=self.app.loop)
            print("ARROW LEFT")
        elif decrypted_message[7] == 13762788:
            if "right" in ARROWS_TO_DACP_COMMAND and ARROWS_TO_DACP_COMMAND["right"] is not None:
                asyncio.ensure_future(make_request_to_uxplay_client(using_session, command=ARROWS_TO_DACP_COMMAND["right"]), loop=self.app.loop)
            print("ARROW RIGHT")



        print('Send: {!r}'.format(message)) # not needed, could cause issues maybe
        self.transport.write(data)

async def directonal_controller_task(app):
    server = await app.loop.create_server(
        lambda: ArrowServerProtocol(app),
        ADDRESS, ARROWS_PORT)
    # server.sockets[0].setsockopt(SOL_SOCKET, SO_KEEPALIVE, 1)
    app[arrow_manager] = asyncio.create_task(server.serve_forever())

    yield
    
    
    app[arrow_manager].cancel()
    await server.close()

async def get_pairable_remotes(request):
    return web.Response(body=str(app[remote_pairing_mdns_entries]), status=200)

async def pair_to_remote(request):
    def get_pairing_code(pin_code, pairing_guid):
        # credit for hash details: pyatv
        merged = StringIO()
        merged.write(pairing_guid)
        for char in str(pin_code).zfill(4):
            merged.write(char)
            merged.write("\x00")
        return md5(merged.getvalue().encode()).hexdigest().upper()

    query = request.url.query
    if 'fqn' not in query:
        return web.Response(body="Must include ?fqn=", status=400)
    fqn = query['fqn']
    if 'pin' not in query:
        return web.Response(body="Must include ?pin=", status=400)
    pin_code = query['pin']
    if fqn not in app[remote_pairing_mdns_entries]:
        return web.Response(body="remote not found", status=404)
    record = app[remote_pairing_mdns_entries][fqn]
    pairing_code = get_pairing_code(pin_code, record.pairing_guid)
    logging.info(f"Attempting to pair to {query['fqn']} with pin {pin_code} and pairing guid {record.pairing_guid} -> {pairing_code=}")
    url = URL("http://127.0.0.1") / "pair" % {'pairingcode': pairing_code, 'servicename': DAAP_SERVER_ID}
    url = url.with_port(record.port).with_host(record.addresses[0][0])
    print(url)
    async with ClientSession() as session:
        async with session.get(url) as resp:
            print(resp.status)
            if resp.status != 200:
                print(f"Pair request failed with status code {resp.status}")
                return web.Response(body="Pair request failed with status code {resp.status}", status=403)
            print("Pair request recieved a response")
            try:
                daap_resp = dmap_parser.parse(await resp.read(), tag_definitions.lookup_tag)
                guid_resp = dmap_parser.first(daap_resp, 'cmpa', 'cmpg')
                name = dmap_parser.first(daap_resp, 'cmpa', 'cmnm')
                device = dmap_parser.first(daap_resp, 'cmpa', 'cmty')
                app[creds][hex(guid_resp)[2:].upper()] = {
                    'cred': hex(guid_resp)[2:].upper(),
                    'pin': pin_code,
                    'record': record,
                    'name': name,
                    'device': device,
                }
                print("Pair success")
                print(app[creds][hex(guid_resp)[2:].upper()])
                return web.Response(body=hex(guid_resp)[2:].upper(), status=200)
            except:
                return web.Response(body="Failed to pair", status=500)

async def get_server_info(request):
    print(request.url)
    mstt = tags.uint32_tag("mstt",200)
    mpro = tags.uint32_tag("mpro",231082)
    minm = tags.string_tag("minm",f"{SERVER_NAME}\x00") # is \x00 needed to signify that string is over??
    apro = tags.uint32_tag("apro",196620)
    aeSV = tags.uint32_tag("aeSV",196618)
    mstm = tags.uint32_tag("mstm",1800)
    msdc = tags.uint32_tag("msdc",1)
    aeFP = tags.uint8_tag("aeFP",2)
    arFR = tags.uint8_tag("arFR",100)
    mslr = tags.bool_tag("mslr",True)
    msal = tags.bool_tag("msal",True)
    mstc = tags.uint32_tag("mstc",int(time.time()))
    msto = tags.uint32_tag("msto",4294938496) # possibly some sort of check to mstc?
    atSV = tags.uint32_tag("atSV",65541)
    ated = tags.uint16_tag("ated",True)
    asgr = tags.uint16_tag("asgr",3)
    asse = tags.uint32_tag("asse",7341056)
    aeSX = tags.uint32_tag("aeSX", 3)
    msed = tags.uint16_tag('msed', True)
    msup = tags.uint16_tag('msup', True)
    mspi = tags.uint16_tag('mspi', True)
    msex = tags.uint16_tag('msex', True)
    msbr = tags.uint16_tag('msbr', True)
    msqy = tags.uint16_tag('msqy', True)
    msix = tags.uint16_tag('msix', True)
    mscu = tags.uint32_tag('mscu', 101)
    # msml = 8 byte integer, macaddress?

    msrv = tags.container_tag('msrv', 
        mstt +
        mpro +
        minm +
        apro +
        aeSV +
        mstm +
        msdc +
        aeFP +
        arFR +
        mslr +
        msal +
        mstc +
        msto +
        atSV +
        ated +
        asgr +
        asse +
        aeSX +
        msed +
        msup +
        mspi +
        msex +
        msbr +
        msqy +
        msix +
        mscu)
    return web.Response(body=msrv, status=200, headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })
    
async def login(request):
    url = request.url
    print(url)
    if 'pairing-guid' not in url.query:
        return web.Response(body=tags.container_tag('mlog',
            tags.uint32_tag('mstt', 503)
        ), status=503, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    pairing_guid = url.query["pairing-guid"] # // todo: actually check this

    session_id = int.from_bytes(random.randbytes(3),"little")
    app[session][str(session_id)] = {}
    print(app[session])
    return web.Response(body=tags.container_tag('mlog',
        tags.uint32_tag('mstt', 200) + 
        tags.uint32_tag('mlid', session_id)
    ), status="200", headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })

async def ctrl_int(request):    
    print(request.url)
    daap_resp = tags.container_tag('caci', 
        tags.uint32_tag('mstt', 200) +
        tags.uint32_tag('mtco', 1) + 
        tags.uint32_tag('mrco', 1) + 
        tags.container_tag('mlcl', tags.container_tag('mlit',
            tags.uint32_tag('miid', 1) + 
            tags.uint32_tag('cmik', 1) + 
            tags.uint32_tag('cmpr', 131074) + 
            tags.uint32_tag('capr', 131077) + 
            tags.uint32_tag('atCV', 65539) + 
            tags.uint32_tag('cmsp', 1) + 
            tags.uint32_tag('cmsb', 1) +
            tags.uint32_tag('aeFR', 100) + 
            tags.uint32_tag('cmsv', 0) + 
            tags.uint32_tag('cmsc', 1) + 
            tags.uint32_tag('cass', 0) + 
            tags.uint32_tag('caov', 0) + 
            tags.uint32_tag('casu', 0) + 
            tags.uint32_tag('ceSG', 0) +
            tags.uint32_tag('ceDR', 1) +
            tags.uint32_tag('cmrl', 1) +
            tags.uint32_tag('ceSX', 0b1011)
        ))  
    )
    return web.Response(body=daap_resp, status="200", headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })

async def play_status_update(request):
    query = request.url.query
    if 'revision-number' in query and query['revision-number'] == "2":
        await asyncio.sleep(60) # wait for a while?
        return web.Response(body=None, status=406, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    daap_resp = tags.container_tag('cmst', tags.uint32_tag('mstt', 200) + tags.uint32_tag('cmsr', 2))
    return web.Response(body=daap_resp, status="200", headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })

async def control_prompt_update(request):
    query = request.url.query
    if 'pairing-guid' not in query:
        logging.warning("pairing guid not given")
        return web.Response(body=None, status=503, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    pairing_guid = query['pairing-guid']
    if 'session-id' not in query:
        logging.warning("session id not given")
        return web.Response(body=None, status=503, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    session_id = query['session-id']
    if session_id not in app[session]:
        logging.warning("session invalid")
        return web.Response(body=None, status=503, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    current_session = app[session][session_id]
    prompt_id_0 = 'prompt-id' in query and query['prompt-id'] == "0"
    prompt_id = query['prompt-id']
    if prompt_id_0:
        logging.info("cont. prpt update w/ prompt-id 0 (initial)")
    else:
        logging.info(f"cont. prpt update w/ prompt-id {prompt_id} (cmte is: {current_session['cmte']} -> {ARROWS_PORT ^ int(current_session["cmte"].split(",")[0])})")
    if (prompt_id_0 is False) and ('cmte' not in current_session):
        return web.Response(body=None, status=400, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    logging.info(f'new prompt = {("9" if prompt_id_0 else (str(int(prompt_id) + 1) if prompt_id == "9" else prompt_id))}')
    if (int(prompt_id) > 9):
        logging.info("waiting 10 seconds due to prompt id")
        await asyncio.sleep(10)
        return web.Response(body=tags.container_tag('cmcp',
            tags.uint32_tag('mstt', 200) + tags.uint32_tag('miid', 0)
        ), status=200, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    daap_resp = tags.container_tag('cmcp',
        tags.uint32_tag('mstt', 200) +
        tags.uint32_tag('miid', (9 if prompt_id_0 else (int(prompt_id) + 1 if prompt_id == "9" else int(prompt_id)))) + #client bumps ?prompt-id to this on next req
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_SubText') +
            tags.string_tag('cmcv',  str(SUB_TEXT))
        ) + 
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_Version') +
            tags.string_tag('cmcv', "0")
        ) +
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_MaxCharacters') +
            tags.string_tag('cmcv', "0")
        ) + 
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_MinCharacters') +
            tags.string_tag('cmcv', "0")
        ) + 
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_SecureText') +
            tags.string_tag('cmcv', "0")
        ) + 
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_KeyboardType') +
            tags.string_tag('cmcv', "0")
        ) + 
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_String') +
            tags.string_tag('cmcv', """
            308202C33082022CA003020102020D3333AF080604AF0001AF000001300D06092A864886F70D0101050500307B3\
            10B300906035504061302555331133011060355040A130A4170706C6520496E632E31263024060355040B131D417\
            0706C652043657274696669636174696F6E20417574686F72697479312F302D060355040313264170706C6520466\
            16972506C61792043657274696669636174696F6E20417574686F72697479301E170D30383036303432313330303\
            15A170D3133303630333231333030315A3066310B300906035504061302555331133011060355040A130A4170706\
            C6520496E632E31173015060355040B130E4170706C652046616972506C61793129302706035504031320526F736\
            9652E333333334146303830363034414630303031414630303030303130819F300D06092A864886F70D010101050\
            003818D0030818902818100DCB60285A26C6B4DE502C49C842A527176C0185B082DCE6C646B55A2640706A6967DE\
            D8F23C8542E284107A9A22709E1056E934BC3C4F01798BD54391829490665205F296E9BE2595E0419AEDEDA77D44\
            560CC7AF1E3A72F37EFE9AED51263ED0807FED2CCB723F51D08CD8DFB41F675770671E03C29E29E39C5316105745\
            3BD0203010001A360305E300E0603551D0F0101FF0404030203B8300C0603551D130101FF04023000301D0603551\
            D0E041604148F4E4787070D6D84FD1F307932107EBC04CEAC55301F0603551D23041830168014FA0DD411911BE6B\
            24E1E06499411DD6362075964300D06092A864886F70D010105050003818100153F2F1572D279E5DB1E1776CCA60\
            3131D7788B598BD1EFC7C1703A40A06C905C762CE1665440912A1BCA88F766861C436543A1A9AB536DEB479BF280\
            3F383E92A75B7360B47B8197387A6BB4EB82554C6762C06C4E236A890139396F56138C1B69395FCFED8CB74BF94D\
            91E0E98F6F8276A2B49172847498A5843847ED00FC8""" # magic number?
            if prompt_id_0 else str(ARROWS_PORT ^ int(current_session["cmte"].split(",")[0])))
        ) +
                tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_TextInputType') +
            tags.string_tag('cmcv', "0")
        ) + 
        (b'' if prompt_id_0 else tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_Title') +
            tags.string_tag('cmcv', pairing_guid)
        )) +
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_MessageType') +
            tags.string_tag('cmcv', "3" if prompt_id_0 else "5")
        ) + 
        tags.container_tag('mdcl', 
            tags.string_tag('cmce', 'kKeybMsgKey_SessionID') +
            tags.string_tag('cmcv', "9"
        ))
    )
    return web.Response(body=daap_resp, status="200", headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })

async def logout(request):
    print(request.url)
    query = request.url.query
    if 'session-id' in query:
        if query['session-id'] in app[session]:
            del app[session][query['session-id']]
    return web.Response(body=None, status="204", headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })

async def get_playqueue_contents(request):
    print(request.url)
    await request.read()
    daap_resp = binascii.unhexlify("636551520000000c6d73747400000004000000c8")
    return web.Response(body=daap_resp, status="200", headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })

async def update_uxplay_dacp_data():
    try:
        async with aiofiles.open("./.uxplay.dacp", "r") as file:
            lines = await file.readlines()
            lines = [line.strip() for line in lines]
            if len(lines) != 2:
                raise Exception("uxplay dacp had bad data!")
            uxplay_data = {
                'dacp_id': lines[0],
                'active_remote': lines[1],
            }
            app[uxplay] = uxplay_data

            
    except Exception as e:
        app[uxplay] = None
        print("Error in reading uxplay file: ", e)
async def make_request_to_uxplay_client(current_session, command, retry=True):
    # find client record]
    uxplay_data = app[uxplay]
    if (uxplay_data is None):
        if (retry is True):
            print("no data for dacp client, loading file!")
            await update_uxplay_dacp_data()
            await make_request_to_uxplay_client(current_session, command, retry=False)
        else:
            print("could not get dacp client data!")
        return
    current_record: Optional[ClientRemoteControlRecord] = None
    for name, record in app[remote_control_mdns_entries].items():
        print(record, uxplay_data["dacp_id"])
        if uxplay_data["dacp_id"] in record.fqn:
            current_record = record
    if current_record is None:
        print("could not find dacp client")
        if retry is True:
            print("reloading uxplay file!")
            await update_uxplay_dacp_data()
            await make_request_to_uxplay_client(current_session, command, retry=False)
        return
    print("Using record", current_record)
    url = URL("http://127.0.0.1") / "ctrl-int" / "1" / command
    url = url.with_port(current_record.port).with_host(record.addresses[0][0])
    print(url)
    async with ClientSession() as session:
        async with session.get(url, headers={
            "Active-Remote": uxplay_data["active_remote"]
        }) as resp:
            print(resp)
            if (resp.status != 200) and (retry is True):
                # some issue with credentials, reload
                print("reloading uxplay file! (for bad credentials?)")
                await update_uxplay_dacp_data()
                await make_request_to_uxplay_client(current_session, command, retry=False)

async def control_prompt_entry(request):
    query = request.url.query
    if 'session-id' not in query:
        return web.Response(body=None, status=503, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    session_id = query['session-id']
    if session_id not in app[session]:
        return web.Response(body=None, status=503, headers={
            "Content-Type": "application/x-dmap-tagged",
            "DAAP-Server": "iTunes/11.1b37 (OS X)",
            "Server": "Darwin",
        })
    current_session = app[session][session_id]
    daap_resp = dmap_parser.parse(await request.read(), tag_definitions.lookup_tag)
    cmbe_resp = dmap_parser.first(daap_resp, 'cmbe')
    print(f"Control Prompt Entry cmbe {cmbe_resp}")
    if cmbe_resp == "DRPortInfoRequest":
        cmte_resp = dmap_parser.first(daap_resp, 'cmte')
        current_session["cmte"] = cmte_resp
        current_session["trackpad_key"] = int.from_bytes((SUB_TEXT ^ int(cmte_resp.split(",")[0])).to_bytes(4, 'little'))
        current_session["trackpad_expected_start_bytes"] = (32 ^ current_session["trackpad_key"]).to_bytes(4)
        print(current_session)
        print(f"DRPortInfoRequest cmte {cmte_resp}")
    elif cmbe_resp in CMBE_COMMAND_TO_DACP_COMMAND and CMBE_COMMAND_TO_DACP_COMMAND[cmbe_resp] is not None:
        await make_request_to_uxplay_client(current_session, command=CMBE_COMMAND_TO_DACP_COMMAND[cmbe_resp])
        

    return web.Response(body=tags.container_tag('ceQE', tags.uint32_tag('mstt', 200)), status=204, headers={
        "Content-Type": "application/x-dmap-tagged",
        "DAAP-Server": "iTunes/11.1b37 (OS X)",
        "Server": "Darwin",
    })

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    app = web.Application()
    mdns_manager = web.AppKey('mdns_manager', asyncio.Task[None])
    remote_pairing_mdns_entries = web.AppKey('remote_pairing_mdns_entries', dict[str, ClientRemotePairingRecord])
    remote_control_mdns_entries = web.AppKey('remote_control_entries', dict[str, ClientRemoteControlRecord])
    

    creds = web.AppKey('creds', dict)
    session = web.AppKey('session', dict)
    arrow_manager = web.AppKey('arrow_manager', asyncio.Task[None])
    uxplay = web.AppKey('uxplay', dict)

    app[creds] = {}
    app[session] = {}
    # app[uxplay] = {
    #     "active_remote": None,
    #     "dacp_id": None,
    # }
    app[uxplay] = None
    app.cleanup_ctx.append(mdns_task)
    app.cleanup_ctx.append(directonal_controller_task)
    app.add_routes([
        web.get('/remotes', get_pairable_remotes),
        web.get('/pair', pair_to_remote),
        web.get('/server-info', get_server_info),
        web.get('/login', login),
        web.post("/ctrl-int", ctrl_int),
        web.get('/ctrl-int/1/playstatusupdate', play_status_update),
        web.post('/ctrl-int/1/controlpromptentry', control_prompt_entry),
        web.get('/controlpromptupdate', control_prompt_update),
        web.get('/logout', logout),
        web.post('/playqueue-contents', get_playqueue_contents),
    ])
    web.run_app(app, port=SERVER_PORT)

