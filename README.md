# DAAPRemoteServer

connect to this server with iPadOS or iOS remote (found in Control Center). You will be able to use the arrows and buttons.

## setting variables
`DAAP_SERVER_ID`, `DAAP_DATABASE_ID`, and `SUB_TEXT`: I reccomend randomly generating these unique to your instance when you first setup. I don't know if you should change them on each startup.

`SERVER_NAME`: the name of the remote that will be displayed on your idevice. I haven't tested non-ascii characters. spaces seem to work.

`SERVER_PORT`: the port that the server will run on.
`ARROWS_PORT`: the port that the server will recieve arrow commands on

`ADDRESS`: the ip address of the machine DAAPRemoteServer is running on. (eg. `ifconfig`). Only tested with ipv4, but will likely work for ipv6.

### firewall note:
for pairing only, the server needs to make an HTTP request to a random port on the iDevice. you will need to allow this in your firewall in case that outgoing connections are blocked. After initial pairing, you will not need outgoing connections...

... unless you also want to control the client connected to the uxplay server, for which outgoing connections will also be needed to a random port. (Ports tend to be 35ish thousand and up?)

you will also obviously need to allow mdns (port 5353) (see notes on uxplay github for mdns debugging issues)


## current/temporary pairing note
you will need to pair the remote to the server using the normal procedure. However, beyond this the server doesn't check if the idevice has a valid pairing key. this feature will come later, so be careful how you expose the service for now.

## using in coordination with UxPlay

In a recent update, [UxPlay](https://github.com/FDH2/Uxplay) can output credentials needed to remotely play/pause/control the mirroring iDevice. you can configure DAAPRemoteServer to read these values and forward them onto the client.

The end result of this is that Device "A" can stream to a UxPlay server normally. Device "B" can then use the built-in ios remote control to connect to a DAAPRemoteServer. Device "B" can then issue up to 8 of the following commands to Device "A" (configure in `ARROWS_TO_DACP_COMMAND` and `CMBE_COMMAND_TO_DACP_COMMAND`:

- beginff 	begin fast forward
- beginrew 	begin rewind
- mutetoggle 	toggle mute status
- nextitem 	play next item in playlist
- previtem 	play previous item in playlist
- pause 	pause playback
- playpause 	toggle between play and pause
- play 	start playback
- stop 	stop playback
- playresume 	play after fast forward or rewind
- shuffle_songs 	shuffle playlist
- volumedown 	turn audio volume down
- volumeup 	turn audio volume up


Set the `-dacp` flag in uxplay (or view latest documentation as this might have changed) and the `UXPLAY_DACP_FILE` in DAAPRemoteServer to the same file 

## credits

[pyatv](https://pyatv.dev) for `dmap_parser.py`, `tags.py`, `tag_definitions.py`. see files for license.

[WpRemote](https://github.com/misenhower/WPRemote) for some encryption details.
