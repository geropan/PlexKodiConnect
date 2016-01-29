import re
import threading
import xbmcgui
from xml.dom.minidom import parseString
from functions import *
from settings import settings
from httppersist import requests
import downloadutils

class SubscriptionManager:
    def __init__(self):
        self.subscribers = {}
        self.info = {}
        self.lastkey = ""
        self.containerKey = ""
        self.playQueueID = ''
        self.playQueueVersion = 1
        self.lastratingkey = ""
        self.volume = 0
        self.guid = ""
        self.server = ""
        self.protocol = "http"
        self.port = ""
        self.playerprops = {}
        self.download = downloadutils.DownloadUtils()
        
    def getVolume(self):
        self.volume = getVolume()

    def msg(self, players):
        msg = getXMLHeader()
        msg += '<MediaContainer commandID="INSERTCOMMANDID"'
        if players:
            self.getVolume()
            maintype = plex_audio()
            for p in players.values():
                if p.get('type') == xbmc_video():
                    maintype = plex_video()
                elif p.get('type') == xbmc_photo():
                    maintype = plex_photo()
            self.mainlocation = "fullScreen" + maintype[0:1].upper() + maintype[1:].lower()
        else:
            self.mainlocation = "navigation"
        msg += ' location="%s">' % self.mainlocation
       
        msg += self.getTimelineXML(getAudioPlayerId(players), plex_audio())
        msg += self.getTimelineXML(getPhotoPlayerId(players), plex_photo())
        msg += self.getTimelineXML(getVideoPlayerId(players), plex_video())
        msg += "\r\n</MediaContainer>"
        return msg
        
    def getTimelineXML(self, playerid, ptype):
        if playerid is not None:
            info = self.getPlayerProperties(playerid)
            # save this info off so the server update can use it too
            self.playerprops[playerid] = info;
            state = info['state']
            time = info['time']
        else:
            state = "stopped"
            time = 0
        ret = "\r\n"+'<Timeline location="%s" state="%s" time="%s" type="%s"' % (self.mainlocation, state, time, ptype)
        if playerid is not None:
            WINDOW = xbmcgui.Window(10000)
            
            # pbmc_server = str(WINDOW.getProperty('plexbmc.nowplaying.server'))
            # userId = str(WINDOW.getProperty('emby_currUser'))
            # pbmc_server = str(WINDOW.getProperty('emby_server%s' % userId))
            pbmc_server = None
            keyid = None
            count = 0
            while not keyid:
                if count > 10:
                    break
                keyid = str(WINDOW.getProperty('Plex_currently_playing_itemid'))
                xbmc.sleep(1000)
                count += 1
            if keyid:
                self.lastkey = "/library/metadata/%s"%keyid
                self.lastratingkey = keyid
                ret += ' containerKey="%s"' % (self.containerKey)
                ret += ' key="%s"' % (self.lastkey)
                ret += ' ratingKey="%s"' % (self.lastratingkey)
                if pbmc_server:
                    (self.server, self.port) = pbmc_server.split(':')
            serv = getServerByHost(self.server)
            if self.playQueueID:
                ret += ' playQueueID="%s"' % self.playQueueID
                ret += ' playQueueVersion="%s"' % self.playQueueVersion
            ret += ' duration="%s"' % info['duration']
            ret += ' seekRange="0-%s"' % info['duration']
            ret += ' controllable="%s"' % self.controllable()
            ret += ' machineIdentifier="%s"' % serv.get('uuid', "")
            ret += ' protocol="%s"' % serv.get('protocol', "http")
            ret += ' address="%s"' % serv.get('server', self.server)
            ret += ' port="%s"' % serv.get('port', self.port)
            ret += ' guid="%s"' % info['guid']
            ret += ' volume="%s"' % info['volume']
            ret += ' shuffle="%s"' % info['shuffle']
        
        ret += '/>'
        return ret
     
    def updateCommandID(self, uuid, commandID):
        if commandID and self.subscribers.get(uuid, False):
            self.subscribers[uuid].commandID = int(commandID)            
        
    def notify(self, event = False):
        self.cleanup()
        players = getPlayers()
        # fetch the message, subscribers or not, since the server
        # will need the info anyway
        msg = self.msg(players)
        if self.subscribers:
            with threading.RLock():
                for sub in self.subscribers.values():
                    sub.send_update(msg, len(players)==0)
        self.notifyServer(players)
        return True
    
    def notifyServer(self, players):
        if not players:
            return True
        params = {'state': 'stopped'}
        for p in players.values():
            info = self.playerprops[p.get('playerid')]
            params = {}
            params['containerKey'] = (self.containerKey or "/library/metadata/900000")
            if self.playQueueID:
                params['playQueueID'] = self.playQueueID
            params['key'] = (self.lastkey or "/library/metadata/900000")
            params['ratingKey'] = (self.lastratingkey or "900000")
            params['state'] = info['state']
            params['time'] = info['time']
            params['duration'] = info['duration']
            params['playQueueVersion'] = self.playQueueVersion
        serv = getServerByHost(self.server)
        url = serv.get('protocol', 'http') + '://' \
            + serv.get('server', 'localhost') + ':' \
            + serv.get('port', 32400) + "/:/timeline"
        self.download.downloadUrl(url, type="GET", parameters=params)
        # requests.getwithparams(serv.get('server', 'localhost'), serv.get('port', 32400), "/:/timeline", params, getPlexHeaders(), serv.get('protocol', 'http'))
        printDebug("params: %s" % params)
        printDebug("players: %s" % players)
        printDebug("sent server notification with state = %s" % params['state'])

    def controllable(self):
        return "volume,shuffle,repeat,audioStream,videoStream,subtitleStream,skipPrevious,skipNext,seekTo,stepBack,stepForward,stop,playPause"
        
    def addSubscriber(self, protocol, host, port, uuid, commandID):
        sub = Subscriber(protocol, host, port, uuid, commandID)
        with threading.RLock():
            self.subscribers[sub.uuid] = sub
        return sub
        
    def removeSubscriber(self, uuid):
        with threading.RLock():
            for sub in self.subscribers.values():
                if sub.uuid == uuid or sub.host == uuid:
                    sub.cleanup()
                    del self.subscribers[sub.uuid]
                    
    def cleanup(self):
        with threading.RLock():
            for sub in self.subscribers.values():
                if sub.age > 30:
                    sub.cleanup()
                    del self.subscribers[sub.uuid]
            
    def getPlayerProperties(self, playerid):
        info = {}
        try:
            # get info from the player
            props = jsonrpc("Player.GetProperties", {"playerid": playerid, "properties": ["time", "totaltime", "speed", "shuffled"]})
            printDebug(jsonrpc("Player.GetItem", {"playerid": playerid, "properties": ["file", "showlink", "episode", "season"]}))
            info['time'] = timeToMillis(props['time'])
            info['duration'] = timeToMillis(props['totaltime'])
            info['state'] = ("paused", "playing")[int(props['speed'])]
            info['shuffle'] = ("0","1")[props.get('shuffled', False)]            
        except:
            info['time'] = 0
            info['duration'] = 0
            info['state'] = "stopped"
            info['shuffle'] = False
        # get the volume from the application
        info['volume'] = self.volume
        info['guid'] = self.guid

        return info

class Subscriber:
    def __init__(self, protocol, host, port, uuid, commandID):
        self.protocol = protocol or "http"
        self.host = host
        self.port = port or 32400
        self.uuid = uuid or host
        self.commandID = int(commandID) or 0
        self.navlocationsent = False
        self.age = 0
        self.download = downloadutils.DownloadUtils()
    def __eq__(self, other):
        return self.uuid == other.uuid
    def tostr(self):
        return "uuid=%s,commandID=%i" % (self.uuid, self.commandID)
    def cleanup(self):
        requests.closeConnection(self.protocol, self.host, self.port)
    def send_update(self, msg, is_nav):
        self.age += 1
        if not is_nav:
            self.navlocationsent = False
        elif self.navlocationsent:
            return True
        else:
            self.navlocationsent = True
        msg = re.sub(r"INSERTCOMMANDID", str(self.commandID), msg)
        printDebug("sending xml to subscriber %s: %s" % (self.tostr(), msg))
        url = self.protocol + '://' + self.host + ':' + self.port \
            + "/:/timeline"
        # Override some headers
        headerOptions = {
            'Content-Range': 'bytes 0-/-1',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_2) AppleWebKit/537.17 (KHTML, like Gecko) Chrome/24.0.1312.52 Safari/537.17',
            'Accept': '*/*',
            'X-Plex-Username': 'croneter',
            'Connection': 'keep-alive',
            'X-Plex-Client-Capabilities': 'protocols=shoutcast,http-video;videoDecoders=h264{profile:high&resolution:1080&level:51};audioDecoders=mp3,aac,dts{bitrate:800000&channels:8},ac3{bitrate:800000&channels:8}',
            'X-Plex-Client-Profile-Extra': 'add-transcode-target-audio-codec(type=videoProfile&context=streaming&protocol=*&audioCodec=dca,ac3)'
        }
        response = self.download.downloadUrl(
            url,
            postBody=msg,
            type="POSTXML",
            headerOptions=headerOptions)
        # if not requests.post(self.host, self.port, "/:/timeline", msg, getPlexHeaders(), self.protocol):
        # subMgr.removeSubscriber(self.uuid)
        if response in [False, None, 401]:
            subMgr.removeSubscriber(self.uuid)
subMgr = SubscriptionManager()    
