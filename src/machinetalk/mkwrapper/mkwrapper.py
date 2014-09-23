#!/usr/bin/python
import os
import sys
import uuid
from stat import *
import zmq
import netifaces
import avahi
import dbus
import thread
import time
import math
import socket

import ConfigParser
import linuxcnc

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from message_pb2 import Container
from config_pb2 import *
from types_pb2 import *
from status_pb2 import *
from preview_pb2 import *
from object_pb2 import ProtocolParameters


def getFreePort():
    sock = socket.socket()
    sock.bind(('', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ZeroconfService:
    """A simple class to publish a network service with zeroconf using
    avahi.
    """

    def __init__(self, name, port, stype="_http._tcp", subtype=None,
                 domain="", host="", text=""):
        self.name = name
        self.stype = stype
        self.domain = domain
        self.host = host
        self.port = port
        self.text = text
        self.subtype = subtype

    def publish(self):
        bus = dbus.SystemBus()
        server = dbus.Interface(
                         bus.get_object(
                                 avahi.DBUS_NAME,
                                 avahi.DBUS_PATH_SERVER),
                        avahi.DBUS_INTERFACE_SERVER)

        g = dbus.Interface(
                    bus.get_object(avahi.DBUS_NAME,
                                   server.EntryGroupNew()),
                    avahi.DBUS_INTERFACE_ENTRY_GROUP)

        g.AddService(avahi.IF_UNSPEC, avahi.PROTO_UNSPEC, dbus.UInt32(0),
                     self.name, self.stype, self.domain, self.host,
                     dbus.UInt16(self.port), self.text)

        if self.subtype:
            g.AddServiceSubtype(avahi.IF_UNSPEC,
                                avahi.PROTO_UNSPEC,
                                dbus.UInt32(0),
                                self.name, self.stype, self.domain,
                                self.subtype)

        g.Commit()
        self.group = g

    def unpublish(self):
        self.group.Reset()


class CustomFTPHandler(FTPHandler):

    def __del__(self):
        for uploadedFile in self.uploadedFiles:
            os.remove(uploadedFile)

    def on_file_received(self, file):
        # do something when a file has been received
        if not hasattr(self, 'uploadedFiles'):
            self.uploadedFiles = set()
        self.uploadedFiles.add(file)

    def on_incomplete_file_received(self, file):
        # remove partially uploaded files
        os.remove(file)


class FileService():

    def __init__(self, iniFile=None, ipv4="", svcUuid=None,
                debug=False):
        self.debug = debug
        self.ipv4 = ipv4

        # Linuxcnc
        try:
            iniFile = iniFile or os.environ.get('INI_FILE_NAME', '/dev/null')
            self.ini = linuxcnc.ini(iniFile)
            self.directory = self.ini.find('DISPLAY', 'PROGRAM_PREFIX') or os.getcwd()
        except linuxcnc.error as detail:
            print(("error", detail))
            sys.exit(1)

        self.filePort = getFreePort()
        self.fileDsname = "ftp://" + self.ipv4 + ":" + str(self.filePort)

        me = uuid.uuid1()
        self.fileTxtrec    = [str('dsn=' + self.fileDsname),
                              str('uuid=' + svcUuid),
                              str('service=' + 'file'),
                              str('instance=' + str(me))]

        if self.debug:
            print(('file: ' + 'dsname = ' + self.fileDsname +
                             ' port = ' + str(self.filePort) +
                             ' txtrec = ' + str(self.fileTxtrec)))

        #FTP
        # Instantiate a dummy authorizer for managing 'virtual' users
        self.authorizer = DummyAuthorizer()

        # anonymous user has full read write access
        self.authorizer.add_anonymous(self.directory, perm="lradw")

        # Instantiate FTP handler class
        self.handler = CustomFTPHandler
        self.handler.authorizer = self.authorizer

        # Define a customized banner (string returned when client connects)
        self.handler.banner = "welcome to the GCode file service"

        # Instantiate FTP server class and listen on some address
        self.address = (self.ipv4, self.filePort)
        self.server = FTPServer(self.address, self.handler)

        # set a limit for connections
        self.server.max_cons = 256
        self.server.max_cons_per_ip = 5

        # Zeroconf
        try:
            self.name = 'File on %s' % self.ipv4
            self.fileService = ZeroconfService(self.name, self.filePort,
                                                stype='_machinekit._tcp',
                                                subtype='_file._sub._machinekit._tcp',
                                                text=self.fileTxtrec)
            self.fileService.publish()
        except Exception as e:
            print (('cannot register DNS service' + str(e)))
            sys.exit(1)

        thread.start_new_thread(self.run, ())

    def run(self):
        try:
            # start ftp server
            self.server.serve_forever()

            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.fileService.unpublish()


class StatusValues():

    def __init__(self):
        self.io = EmcStatusIo()
        self.config = EmcStatusConfig()
        self.motion = EmcStatusMotion()
        self.task = EmcStatusTask()
        self.interp = EmcStatusInterp()

    def clear(self):
        self.io.Clear()
        self.config.Clear()
        self.motion.Clear()
        self.task.Clear()
        self.interp.Clear()


class LinuxCNCWrapper():

    def __init__(self, context, statusUri, errorUri, commandUri,
                iniFile=None, ipv4="", svcUuid=None,
                pollInterval=None, pingInterval=2, debug=False):
        self.debug = debug
        self.ipv4 = ipv4
        self.pingInterval = pingInterval

        # status
        self.status = StatusValues()
        self.txStatus = StatusValues()
        self.motionSubscriptions = 0
        self.motionFullUpdate = False
        self.motionFirstrun = True
        self.ioSubscriptions = 0
        self.ioFullUpdate = False
        self.ioFirstrun = True
        self.taskSubscriptions = 0
        self.taskFullUpdate = False
        self.taskFirstrun = True
        self.configSubscriptions = 0
        self.configFullUpdate = False
        self.configFirstrun = True
        self.interpSubscriptions = 0
        self.interpFullUpdate = False
        self.interpFirstrun = True
        self.totalSubscriptions = 0
        self.textSubscriptions = 0
        self.displaySubscriptions = 0
        self.errorSubscriptions = 0
        self.totalErrorSubscriptions = 0
        self.newErrorSubscription = False

        # Linuxcnc
        try:
            self.stat = linuxcnc.stat()
            self.command = linuxcnc.command()
            self.error = linuxcnc.error_channel()

            iniFile = iniFile or os.environ.get('INI_FILE_NAME', '/dev/null')
            self.ini = linuxcnc.ini(iniFile)
            self.directory = self.ini.find('DISPLAY', 'PROGRAM_PREFIX') or os.getcwd()
            self.pollInterval = pollInterval or self.ini.find('DISPLAY', 'CYCLE_TIME') or 0.1
        except linuxcnc.error as detail:
            print(("error", detail))
            sys.exit(1)

        if self.pingInterval > 0:
            self.pingRatio = math.floor(self.pingInterval / self.pollInterval)
        else:
            self.pingRatio = -1
        self.pingCount = 0

        self.rx = Container()
        self.tx = Container()
        self.rx.type = MT_PING
        self.context = context
        self.statusSocket = context.socket(zmq.XPUB)
        self.statusPort = self.statusSocket.bind_to_random_port(statusUri)
        self.statusDsname = self.statusSocket.get_string(zmq.LAST_ENDPOINT, encoding='utf-8')
        self.errorSocket = context.socket(zmq.XPUB)
        self.errorPort = self.errorSocket.bind_to_random_port(errorUri)
        self.errorDsname = self.errorSocket.get_string(zmq.LAST_ENDPOINT, encoding='utf-8')
        self.commandSocket = context.socket(zmq.DEALER)
        self.commandPort = self.commandSocket.bind_to_random_port(commandUri)
        self.commandDsname = self.commandSocket.get_string(zmq.LAST_ENDPOINT, encoding='utf-8')

        me = uuid.uuid1()
        self.statusTxtrec  = [str('dsn=' + self.statusDsname),
                              str('uuid=' + svcUuid),
                              str('service=' + 'status'),
                              str('instance=' + str(me))]
        self.errorTxtrec   = [str('dsn=' + self.errorDsname),
                              str('uuid=' + svcUuid),
                              str('service=' + 'error'),
                              str('instance=' + str(me))]
        self.commandTxtrec = [str('dsn=' + self.commandDsname),
                              str('uuid=' + svcUuid),
                              str('service=' + 'command'),
                              str('instance=' + str(me))]

        if self.debug:
            print(('status: ' + 'dsname = ' + self.statusDsname +
                               ' port = ' + str(self.statusPort) +
                               ' txtrec = ' + str(self.statusTxtrec)))
            print(('error: ' + 'dsname = ' + self.errorDsname +
                              ' port = ' + str(self.errorPort) +
                              ' txtrec = ' + str(self.errorTxtrec)))
            print(('command: ' + 'dsname = ' + self.commandDsname +
                               ' port = ' + str(self.commandPort) +
                               ' txtrec = ' + str(self.commandTxtrec)))

        poll = zmq.Poller()
        poll.register(self.statusSocket, zmq.POLLIN)
        poll.register(self.errorSocket, zmq.POLLIN)
        poll.register(self.commandSocket, zmq.POLLIN)

        # Zeroconf
        try:
            self.name = 'Status on %s' % self.ipv4
            self.statusService = ZeroconfService(self.name, self.statusPort,
                                                stype='_machinekit._tcp',
                                                subtype='_status._sub._machinekit._tcp',
                                                text=self.statusTxtrec)
            self.statusService.publish()
            self.name = 'Error on %s' % self.ipv4
            self.errorService = ZeroconfService(self.name, self.errorPort,
                                                stype='_machinekit._tcp',
                                                subtype='_error._sub._machinekit._tcp',
                                                text=self.errorTxtrec)
            self.errorService.publish()
            self.name = 'Command on %s' % self.ipv4
            self.commandService = ZeroconfService(self.name, self.commandPort,
                                                stype='_machinekit._tcp',
                                                subtype='_command._sub._machinekit._tcp',
                                                text=self.commandTxtrec)
            self.commandService.publish()
        except Exception as e:
            print (('cannot register DNS service' + str(e)))
            sys.exit(1)

        thread.start_new_thread(self.poll, ())

        try:
            while True:
                s = dict(poll.poll())
                if self.statusSocket in s:
                    self.process_status(self.statusSocket)
                if self.errorSocket in s:
                    self.process_error(self.errorSocket)
                if self.commandSocket in s:
                    self.process_command(self.commandSocket)
        except KeyboardInterrupt:
            self.statusService.unpublish()
            self.errorService.unpublish()
            self.commandService.unpublish()

    def notEqual(self, a, b):
        threshold = 0.0001
        return abs(a - b) > threshold

    def zero_position(self):
        position = Position()
        position.x = 0.0
        position.y = 0.0
        position.z = 0.0
        position.a = 0.0
        position.b = 0.0
        position.c = 0.0
        position.u = 0.0
        position.v = 0.0
        position.w = 0.0
        return position

    def check_position(self, oldPosition, newPosition):
        modified = False
        txPosition = Position()

        if self.notEqual(oldPosition.x, newPosition[0]):
            txPosition.x = newPosition[0]
            modified = True
        if self.notEqual(oldPosition.y, newPosition[1]):
            txPosition.y = newPosition[1]
            modified = True
        if self.notEqual(oldPosition.z, newPosition[2]):
            txPosition.z = newPosition[2]
            modified = True
        if self.notEqual(oldPosition.a, newPosition[3]):
            txPosition.a = newPosition[3]
            modified = True
        if self.notEqual(oldPosition.b, newPosition[4]):
            txPosition.b = newPosition[4]
            modified = True
        if self.notEqual(oldPosition.c, newPosition[5]):
            txPosition.c = newPosition[5]
            modified = True
        if self.notEqual(oldPosition.u, newPosition[6]):
            txPosition.u = newPosition[6]
            modified = True
        if self.notEqual(oldPosition.v, newPosition[7]):
            txPosition.v = newPosition[7]
            modified = True
        if self.notEqual(oldPosition.w, newPosition[8]):
            txPosition.w = newPosition[8]
            modified = True

        if modified:
            return True, txPosition
        else:
            del txPosition
            return False, None

    def update_config(self, stat):
        modified = False

        if self.configFirstrun:
            self.status.config.acceleration = 0.0
            self.status.config.angular_units = 0.0
            self.status.config.axes = 0
            self.status.config.axis_mask = 0
            self.status.config.cycle_time = 0.0
            self.status.config.debug = 0
            self.status.config.kinematics_type = 0
            self.status.config.linear_units = 0.0
            self.status.config.max_acceleration = 0.0
            self.status.config.max_velocity = 0.0
            self.status.config.program_units = 0
            self.status.config.velocity = 0.0
            self.status.config.position_offset = 0
            self.status.config.position_feedback = 0
            self.status.config.max_feed_override = 0.0
            self.status.config.min_feed_override = 0.0
            self.status.config.max_spindle_override = 0.0
            self.status.config.min_spindle_override = 0.0
            self.status.config.default_spindle_speed = 0.0
            self.status.config.default_linear_velocity = 0.0
            self.status.config.min_velocity = 0.0
            self.status.config.max_linear_velocity = 0.0
            self.status.config.min_linear_velocity = 0.0
            self.status.config.default_angular_velocity = 0.0
            self.status.config.max_angular_velocity = 0.0
            self.status.config.min_angular_velocity = 0.0
            self.status.config.increments = ""
            self.status.config.grids = ""
            self.status.config.lathe = False
            self.status.config.geometry = ""
            self.status.config.arcdivision = 0
            self.status.config.no_force_homing = False
            self.configFirstrun = False

            extensions = self.ini.findall("FILTER", "PROGRAM_EXTENSION")
            txExtension = EmcProgramExtension()
            for index, extension in enumerate(extensions):
                txExtension.Clear()
                extensionModified = False

                if len(extensions) == index:
                    self.status.config.program_extension.add()
                    self.status.config.program_extension[index].index = index
                    self.status.config.program_extension[index].extension = ""

                if self.status.config.program_extension[index].extension != extension:
                    self.status.config.program_extension[index].extension = extension
                    txExtension.extension = extension
                    extensionModified = True

                if extensionModified:
                    txExtension.index = index
                    self.txStatus.config.program_extension.add().CopyFrom(txExtension)
                    modified = True
            del txExtension

            positionOffset = self.ini.find('DISPLAY', 'POSITION_OFFSET') or 'RELATIVE'
            if positionOffset == 'MACHINE':
                positionOffset = EMC_CONFIG_MACHINE_OFFSET
            else:
                positionOffset = EMC_CONFIG_RELATIVE_OFFSET
            if (self.status.config.position_offset != positionOffset):
                self.status.config.position_offset = positionOffset
                self.txStatus.config.position_offset = positionOffset
                modified = True

            positionFeedback = self.ini.find('DISPLAY', 'POSITION_OFFSET') or 'ACTUAL'
            if positionFeedback == 'COMMANDED':
                positionFeedback = EMC_CONFIG_COMMANDED_FEEDBACK
            else:
                positionFeedback = EMC_CONFIG_ACTUAL_FEEDBACK
            if (self.status.config.position_feedback != positionFeedback):
                self.status.config.position_feedback = positionFeedback
                self.txStatus.config.position_feedback = positionFeedback
                modified = True

            maxFeedOverride = float(self.ini.find('DISPLAY', 'MAX_FEED_OVERRIDE') or 1.2)
            if (self.status.config.max_feed_override != maxFeedOverride):
                self.status.config.max_feed_override = maxFeedOverride
                self.txStatus.config.max_feed_override = maxFeedOverride
                modified = True

            minFeedOverride = float(self.ini.find('DISPLAY', 'MIN_FEED_OVERRIDE') or 0.5)
            if (self.status.config.min_feed_override != minFeedOverride):
                self.status.config.min_feed_override = minFeedOverride
                self.txStatus.config.min_feed_override = minFeedOverride
                modified = True

            maxSpindleOverride = float(self.ini.find('DISPLAY', 'MAX_SPINDLE_OVERRIDE') or 1.0)
            if (self.status.config.max_spindle_override != maxSpindleOverride):
                self.status.config.max_spindle_override = maxSpindleOverride
                self.txStatus.config.max_spindle_override = maxSpindleOverride
                modified = True

            minSpindleOverride = float(self.ini.find('DISPLAY', 'MIN_SPINDLE_OVERRIDE') or 0.5)
            if (self.status.config.min_spindle_override != minSpindleOverride):
                self.status.config.min_spindle_override = minSpindleOverride
                self.txStatus.config.min_spindle_override = minSpindleOverride
                modified = True

            defaultSpindleSpeed = float(self.ini.find('DISPLAY', 'DEFAULT_SPINDLE_SPEED') or 1)
            if (self.status.config.default_spindle_speed != defaultSpindleSpeed):
                self.status.config.default_spindle_speed = defaultSpindleSpeed
                self.txStatus.config.default_spindle_speed = defaultSpindleSpeed
                modified = True

            defaultLinearVelocity = float(self.ini.find('DISPLAY', 'DEFAULT_LINEAR_VELOCITY') or 0.25)
            if (self.status.config.default_linear_velocity != defaultLinearVelocity):
                self.status.config.default_linear_velocity = defaultLinearVelocity
                self.txStatus.config.default_linear_velocity = defaultLinearVelocity
                modified = True

            minVelocity = float(self.ini.find('DISPLAY', 'MIN_VELOCITY') or 0.01)
            if (self.status.config.min_velocity != minVelocity):
                self.status.config.min_velocity = minVelocity
                self.txStatus.config.min_velocity = minVelocity
                modified = True

            maxLinearVelocity = float(self.ini.find('DISPLAY', 'MAX_LINEAR_VELOCITY') or 1.00)
            if (self.status.config.max_linear_velocity != maxLinearVelocity):
                self.status.config.max_linear_velocity = maxLinearVelocity
                self.txStatus.config.max_linear_velocity = maxLinearVelocity
                modified = True

            minLinearVelocity = float(self.ini.find('DISPLAY', 'MIN_LINEAR_VELOCITY') or 0.01)
            if (self.status.config.min_linear_velocity != minLinearVelocity):
                self.status.config.min_linear_velocity = minLinearVelocity
                self.txStatus.config.min_linear_velocity = minLinearVelocity
                modified = True

            defaultAngularVelocity = float(self.ini.find('DISPLAY', 'DEFAULT_ANGULAR_VELOCITY') or 0.25)
            if (self.status.config.default_angular_velocity != defaultAngularVelocity):
                self.status.config.default_angular_velocity = defaultAngularVelocity
                self.txStatus.config.default_angular_velocity = defaultAngularVelocity
                modified = True

            maxAngularVelocity = float(self.ini.find('DISPLAY', 'MAX_ANGULAR_VELOCITY') or 1.00)
            if (self.status.config.max_angular_velocity != maxAngularVelocity):
                self.status.config.max_angular_velocity = maxAngularVelocity
                self.txStatus.config.max_angular_velocity = maxAngularVelocity
                modified = True

            minAngularVelocity = float(self.ini.find('DISPLAY', 'MIN_ANGULAR_VELOCITY') or 0.01)
            if (self.status.config.min_angular_velocity != minAngularVelocity):
                self.status.config.min_angular_velocity = minAngularVelocity
                self.txStatus.config.min_angular_velocity = minAngularVelocity
                modified = True

            increments = self.ini.find('DISPLAY', 'INCREMENTS') or ''
            if (self.status.config.increments != increments):
                self.status.config.increments = increments
                self.txStatus.config.increments = increments
                modified = True

            grids = self.ini.find('DISPLAY', 'GRIDS') or ''
            if (self.status.config.grids != grids):
                self.status.config.grids = grids
                self.txStatus.config.grids = grids
                modified = True

            lathe = bool(self.ini.find('DISPLAY', 'LATHE') or False)
            if (self.status.config.lathe != lathe):
                self.status.config.lathe = lathe
                self.txStatus.config.lathe = lathe
                modified = True

            geometry = self.ini.find('DISPLAY', 'GEOMETRY') or ''
            if (self.status.config.geometry != geometry):
                self.status.config.geometry = geometry
                self.txStatus.config.geometry = geometry
                modified = True

            arcdivision = int(self.ini.find('DISPLAY', 'ARCDIVISION') or 64)
            if (self.status.config.arcdivision != arcdivision):
                self.status.config.arcdivision = arcdivision
                self.txStatus.config.arcdivision = arcdivision
                modified = True

            noForceHoming = bool(self.ini.find('TRAJ', 'NO_FORCE_HOMING') or False)
            if (self.status.config.no_force_homing != noForceHoming):
                self.status.config.no_force_homing = noForceHoming
                self.txStatus.config.no_force_homing = noForceHoming
                modified = True

        if self.notEqual(self.status.config.acceleration, stat.acceleration):
            self.status.config.acceleration = stat.acceleration
            self.txStatus.config.acceleration = stat.acceleration
            modified = True

        if self.notEqual(self.status.config.angular_units, stat.angular_units):
            self.status.config.angular_units = stat.angular_units
            self.txStatus.config.angular_units = stat.angular_units
            modified = True

        if (self.status.config.axes != stat.axes):
            self.status.config.axes = stat.axes
            self.txStatus.config.axes = stat.axes
            modified = True

        txAxis = EmcStatusConfigAxis()
        for index, axis in enumerate(stat.axis):
            txAxis.Clear()
            axisModified = False

            if index == stat.axes:
                break

            if len(self.status.config.axis) == index:
                self.status.config.axis.add()
                self.status.config.axis[index].index = index
                self.status.config.axis[index].axisType = 0
                self.status.config.axis[index].backlash = 0.0
                self.status.config.axis[index].max_ferror = 0.0
                self.status.config.axis[index].max_position_limit = 0.0
                self.status.config.axis[index].min_ferror = 0.0
                self.status.config.axis[index].min_position_limit = 0.0
                self.status.config.axis[index].units = 0.0

            if self.status.config.axis[index].axisType != axis['axisType']:
                self.status.config.axis[index].axisType = axis['axisType']
                txAxis.axisType = axis['axisType']
                axisModified = True

            if self.notEqual(self.status.config.axis[index].backlash, axis['backlash']):
                self.status.config.axis[index].backlash = axis['backlash']
                txAxis.backlash = axis['backlash']
                axisModified = True

            if self.notEqual(self.status.config.axis[index].max_ferror, axis['max_ferror']):
                self.status.config.axis[index].max_ferror = axis['max_ferror']
                txAxis.max_ferror = axis['max_ferror']
                axisModified = True

            if self.notEqual(self.status.config.axis[index].max_position_limit, axis['max_position_limit']):
                self.status.config.axis[index].max_position_limit = axis['max_position_limit']
                txAxis.max_position_limit = axis['max_position_limit']
                axisModified = True

            if self.notEqual(self.status.config.axis[index].min_ferror, axis['min_ferror']):
                self.status.config.axis[index].min_ferror = axis['min_ferror']
                txAxis.min_ferror = axis['min_ferror']
                axisModified = True

            if self.notEqual(self.status.config.axis[index].min_position_limit, axis['min_position_limit']):
                self.status.config.axis[index].min_position_limit = axis['min_position_limit']
                txAxis.min_position_limit = axis['min_position_limit']
                axisModified = True

            if self.notEqual(self.status.config.axis[index].units, axis['units']):
                self.status.config.axis[index].units = axis['units']
                txAxis.units = axis['units']
                axisModified = True

            if axisModified:
                txAxis.index = index
                self.txStatus.config.axis.add().CopyFrom(txAxis)
                modified = True

        del txAxis

        if (self.status.config.axis_mask != stat.axis_mask):
            self.status.config.axis_mask = stat.axis_mask
            self.txStatus.config.axis_mask = stat.axis_mask
            modified = True

        if self.notEqual(self.status.config.cycle_time, stat.cycle_time):
            self.status.config.cycle_time = stat.cycle_time
            self.txStatus.config.cycle_time = stat.cycle_time
            modified = True

        if (self.status.config.debug != stat.debug):
            self.status.config.debug = stat.debug
            self.txStatus.config.debug = stat.debug
            modified = True

        if (self.status.config.kinematics_type != stat.kinematics_type):
            self.status.config.kinematics_type = stat.kinematics_type
            self.txStatus.config.kinematics_type = stat.kinematics_type
            modified = True

        if self.notEqual(self.status.config.linear_units, stat.linear_units):
            self.status.config.linear_units = stat.linear_units
            self.txStatus.config.linear_units = stat.linear_units
            modified = True

        if self.notEqual(self.status.config.max_acceleration, stat.max_acceleration):
            self.status.config.max_acceleration = stat.max_acceleration
            self.txStatus.config.max_acceleration = stat.max_acceleration
            modified = True

        if self.notEqual(self.status.config.max_velocity, stat.max_velocity):
            self.status.config.max_velocity = stat.max_velocity
            self.txStatus.config.max_velocity = stat.max_velocity
            modified = True

        if (self.status.config.program_units != stat.program_units):
            self.status.config.program_units = stat.program_units
            self.txStatus.config.program_units = stat.program_units
            modified = True

        if self.notEqual(self.status.config.velocity, stat.velocity):
            self.status.config.velocity = stat.velocity
            self.txStatus.config.velocity = stat.velocity
            modified = True

        if self.configFullUpdate:
            self.add_pparams()
            self.send_config(self.status.config, MT_EMCSTAT_FULL_UPDATE)
            self.configFullUpdate = False
        elif modified:
            self.send_config(self.txStatus.config, MT_EMCSTAT_INCREMENTAL_UPDATE)

    def update_io(self, stat):
        modified = False

        if self.ioFirstrun:
            self.status.io.estop = 0
            self.status.io.flood = 0
            self.status.io.lube = 0
            self.status.io.lube_level = 0
            self.status.io.mist = 0
            self.status.io.pocket_prepped = 0
            self.status.io.tool_in_spindle = 0
            self.status.io.tool_offset.MergeFrom(self.zero_position())
            self.ioFirstrun = False

        if (self.status.io.estop != stat.estop):
            self.status.io.estop = stat.estop
            self.txStatus.io.estop = stat.estop
            modified = True

        if (self.status.io.flood != stat.flood):
            self.status.io.flood = stat.flood
            self.txStatus.io.flood = stat.flood
            modified = True

        if (self.status.io.lube != stat.lube):
            self.status.io.lube = stat.lube
            self.txStatus.io.lube = stat.lube
            modified = True

        if (self.status.io.lube_level != stat.lube_level):
            self.status.io.lube_level = stat.lube_level
            self.txStatus.io.lube_level = stat.lube_level
            modified = True

        if (self.status.io.mist != stat.mist):
            self.status.io.mist = stat.mist
            self.txStatus.io.mist = stat.mist
            modified = True

        if (self.status.io.pocket_prepped != stat.pocket_prepped):
            self.status.io.pocket_prepped = stat.pocket_prepped
            self.txStatus.io.pocket_prepped = stat.pocket_prepped
            modified = True

        if (self.status.io.tool_in_spindle != stat.tool_in_spindle):
            self.status.io.tool_in_spindle = stat.tool_in_spindle
            self.txStatus.io.tool_in_spindle = stat.tool_in_spindle
            modified = True

        positionModified = False
        txPosition = None
        positionModified, txPosition = self.check_position(self.status.io.tool_offset, stat.tool_offset)
        if positionModified:
            self.status.io.tool_offset.CopyFrom(txPosition)
            self.txStatus.io.tool_offset.MergeFrom(txPosition)
            modified = True

        txToolResult = EmcToolData()
        for index, toolResult in enumerate(stat.tool_table):
            txToolResult.Clear()
            toolResultModified = False

            if len(self.status.io.tool_table) == index:
                self.status.io.tool_table.add()
                self.status.io.tool_table[index].index = index
                self.status.io.tool_table[index].id = 0
                self.status.io.tool_table[index].xOffset = 0.0
                self.status.io.tool_table[index].yOffset = 0.0
                self.status.io.tool_table[index].zOffset = 0.0
                self.status.io.tool_table[index].aOffset = 0.0
                self.status.io.tool_table[index].bOffset = 0.0
                self.status.io.tool_table[index].cOffset = 0.0
                self.status.io.tool_table[index].uOffset = 0.0
                self.status.io.tool_table[index].vOffset = 0.0
                self.status.io.tool_table[index].wOffset = 0.0
                self.status.io.tool_table[index].diameter = 0.0
                self.status.io.tool_table[index].frontangle = 0.0
                self.status.io.tool_table[index].backangle = 0.0
                self.status.io.tool_table[index].orientation = 0

            if toolResult.id == -1:
                continue

            if self.status.io.tool_table[index].id != toolResult.id:
                self.status.io.tool_table[index].id = toolResult.id
                txToolResult.id = toolResult.id
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].xOffset, toolResult.xoffset):
                self.status.io.tool_table[index].xOffset = toolResult.xoffset
                txToolResult.xOffset = toolResult.xoffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].yOffset, toolResult.yoffset):
                self.status.io.tool_table[index].yOffset = toolResult.yoffset
                txToolResult.yOffset = toolResult.yoffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].zOffset, toolResult.zoffset):
                self.status.io.tool_table[index].zOffset = toolResult.zoffset
                txToolResult.zOffset = toolResult.zoffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].aOffset, toolResult.aoffset):
                self.status.io.tool_table[index].aOffset = toolResult.aoffset
                txToolResult.aOffset = toolResult.aoffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].bOffset, toolResult.boffset):
                self.status.io.tool_table[index].bOffset = toolResult.boffset
                txToolResult.bOffset = toolResult.boffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].cOffset, toolResult.coffset):
                self.status.io.tool_table[index].cOffset = toolResult.coffset
                txToolResult.cOffset = toolResult.coffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].uOffset, toolResult.uoffset):
                self.status.io.tool_table[index].uOffset = toolResult.uoffset
                txToolResult.uOffset = toolResult.uoffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].vOffset, toolResult.voffset):
                self.status.io.tool_table[index].vOffset = toolResult.voffset
                txToolResult.vOffset = toolResult.voffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].wOffset, toolResult.woffset):
                self.status.io.tool_table[index].wOffset = toolResult.woffset
                txToolResult.wOffset = toolResult.woffset
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].diameter, toolResult.diameter):
                self.status.io.tool_table[index].diameter = toolResult.diameter
                txToolResult.diameter = toolResult.diameter
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].frontangle, toolResult.frontangle):
                self.status.io.tool_table[index].frontangle = toolResult.frontangle
                txToolResult.frontangle = toolResult.frontangle
                toolResultModified = True

            if self.notEqual(self.status.io.tool_table[index].backangle, toolResult.backangle):
                self.status.io.tool_table[index].backangle = toolResult.backangle
                txToolResult.backangle = toolResult.backangle
                toolResultModified = True

            if self.status.io.tool_table[index].orientation != toolResult.orientation:
                self.status.io.tool_table[index].orientation = toolResult.orientation
                txToolResult.orientation = toolResult.orientation
                toolResultModified = True

            if toolResultModified:
                txToolResult.index = index
                self.txStatus.io.tool_table.add().CopyFrom(txToolResult)
                modified = True
        del txToolResult

        if self.ioFullUpdate:
            self.add_pparams()
            self.send_io(self.status.io, MT_EMCSTAT_FULL_UPDATE)
            self.ioFullUpdate = False
        elif modified:
            self.send_io(self.txStatus.io, MT_EMCSTAT_INCREMENTAL_UPDATE)

    def update_task(self, stat):
        modified = False

        if self.taskFirstrun:
            self.status.task.echo_serial_number = 0
            self.status.task.exec_state = 0
            self.status.task.file = ""
            self.status.task.input_timeout = False
            self.status.task.optional_stop = False
            self.status.task.read_line = 0
            self.status.task.task_mode = 0
            self.status.task.task_paused = 0
            self.status.task.task_state = 0
            self.taskFirstrun = False

        if (self.status.task.echo_serial_number != stat.echo_serial_number):
            self.status.task.echo_serial_number = stat.echo_serial_number
            self.txStatus.task.echo_serial_number = stat.echo_serial_number
            modified = True

        if (self.status.task.exec_state != stat.exec_state):
            self.status.task.exec_state = stat.exec_state
            self.txStatus.task.exec_state = stat.exec_state
            modified = True

        if (self.status.task.file != stat.file):
            self.status.task.file = stat.file
            self.txStatus.task.file = stat.file
            modified = True

        if (self.status.task.input_timeout != stat.input_timeout):
            self.status.task.input_timeout = stat.input_timeout
            self.txStatus.task.input_timeout = stat.input_timeout
            modified = True

        if (self.status.task.optional_stop != stat.optional_stop):
            self.status.task.optional_stop = stat.optional_stop
            self.txStatus.task.optional_stop = stat.optional_stop
            modified = True

        if (self.status.task.read_line != stat.read_line):
            self.status.task.read_line = stat.read_line
            self.txStatus.task.read_line = stat.read_line
            modified = True

        if (self.status.task.task_mode != stat.task_mode):
            self.status.task.task_mode = stat.task_mode
            self.txStatus.task.task_mode = stat.task_mode
            modified = True

        if (self.status.task.task_paused != stat.task_paused):
            self.status.task.task_paused = stat.task_paused
            self.txStatus.task.task_paused = stat.task_paused
            modified = True

        if (self.status.task.task_state != stat.task_state):
            self.status.task.task_state = stat.task_state
            self.txStatus.task.task_state = stat.task_state
            modified = True

        if self.taskFullUpdate:
            self.add_pparams()
            self.send_task(self.status.task, MT_EMCSTAT_FULL_UPDATE)
            self.taskFullUpdate = False
        elif modified:
            self.send_task(self.txStatus.task, MT_EMCSTAT_INCREMENTAL_UPDATE)

    def update_interp(self, stat):
        modified = False

        if self.interpFirstrun:
            self.status.interp.command = ""
            self.status.interp.interp_state = 0
            self.status.interp.interpreter_errcode = 0
            self.interpFirstrun = False

        if (self.status.interp.command != stat.command):
            self.status.interp.command = stat.command
            self.txStatus.interp.command = stat.command
            modified = True

        txStatusGCode = EmcStatusGCode()
        for index, gcode in enumerate(stat.gcodes):
            txStatusGCode.Clear()
            gcodeModified = False

            if len(self.status.interp.gcodes) == index:
                self.status.interp.gcodes.add()
                self.status.interp.gcodes[index].index = index
                self.status.interp.gcodes[index].value = 0

            if self.status.interp.gcodes[index].value != gcode:
                self.status.interp.gcodes[index].value = gcode
                txStatusGCode.value = gcode
                gcodeModified = True

            if gcodeModified:
                txStatusGCode.index = index
                self.txStatus.interp.gcodes.add().CopyFrom(txStatusGCode)
                modified = True

        del txStatusGCode

        if (self.status.interp.interp_state != stat.interp_state):
            self.status.interp.interp_state = stat.interp_state
            self.txStatus.interp.interp_state = stat.interp_state
            modified = True

        if (self.status.interp.interpreter_errcode != stat.interpreter_errcode):
            self.status.interp.interpreter_errcode = stat.interpreter_errcode
            self.txStatus.interp.interpreter_errcode = stat.interpreter_errcode
            modified = True

        txStatusMCode = EmcStatusMCode()
        for index, mcode in enumerate(stat.mcodes):
            txStatusMCode.Clear()
            mcodeModified = False

            if len(self.status.interp.mcodes) == index:
                self.status.interp.mcodes.add()
                self.status.interp.mcodes[index].index = index
                self.status.interp.mcodes[index].value = 0

            if self.status.interp.mcodes[index].value != mcode:
                self.status.interp.mcodes[index].value = mcode
                txStatusMCode.value = mcode
                mcodeModified = True

            if mcodeModified:
                txStatusMCode.index = index
                self.txStatus.interp.mcodes.add().CopyFrom(txStatusMCode)
                modified = True

        del txStatusMCode

        txStatusSetting = EmcStatusSetting()
        for index, setting in enumerate(stat.settings):
            txStatusSetting.Clear()
            settingModified = False

            if len(self.status.interp.settings) == index:
                self.status.interp.settings.add()
                self.status.interp.settings[index].index = index
                self.status.interp.settings[index].value = 0.0

            if self.notEqual(self.status.interp.settings[index].value, setting):
                self.status.interp.settings[index].value = setting
                txStatusSetting.value = setting
                settingModified = True

            if settingModified:
                txStatusSetting.index = index
                self.txStatus.interp.settings.add().CopyFrom(txStatusSetting)
                modified = True

        del txStatusSetting

        if self.interpFullUpdate:
            self.add_pparams()
            self.send_interp(self.status.interp, MT_EMCSTAT_FULL_UPDATE)
            self.interpFullUpdate = False
        elif modified:
            self.send_interp(self.txStatus.interp, MT_EMCSTAT_INCREMENTAL_UPDATE)

    def update_motion(self, stat):
        modified = False

        if self.motionFirstrun:
            self.status.motion.active_queue = 0
            self.status.motion.actual_position.MergeFrom(self.zero_position())
            self.status.motion.adaptive_feed_enabled = False
            self.status.motion.block_delete = False
            self.status.motion.current_line = 0
            self.status.motion.current_vel = 0.0
            self.status.motion.delay_left = 0.0
            self.status.motion.distance_to_go = 0.0
            self.status.motion.dtg.MergeFrom(self.zero_position())
            self.status.motion.enabled = False
            self.status.motion.feed_hold_enabled = False
            self.status.motion.feed_override_enabled = False
            self.status.motion.feedrate = 0.0
            self.status.motion.g5x_index = 0
            self.status.motion.g5x_offset.MergeFrom(self.zero_position())
            self.status.motion.g92_offset.MergeFrom(self.zero_position())
            self.status.motion.id = 0
            self.status.motion.inpos = False
            self.status.motion.joint_actual_position.MergeFrom(self.zero_position())
            self.status.motion.joint_position.MergeFrom(self.zero_position())
            self.status.motion.motion_line = 0
            self.status.motion.motion_type = 0
            self.status.motion.motion_mode = 0
            self.status.motion.paused = False
            self.status.motion.position.MergeFrom(self.zero_position())
            self.status.motion.probe_tripped = False
            self.status.motion.probe_val = 0
            self.status.motion.probed_position.MergeFrom(self.zero_position())
            self.status.motion.probing = False
            self.status.motion.queue = 0
            self.status.motion.queue_full = False
            self.status.motion.rotation_xy = 0.0
            self.status.motion.spindle_brake = 0
            self.status.motion.spindle_direction = 0
            self.status.motion.spindle_enabled = 0
            self.status.motion.spindle_increasing = 0
            self.status.motion.spindle_override_enabled = False
            self.status.motion.spindle_speed = 0.0
            self.status.motion.spindlerate = 0.0
            self.status.motion.state = 0
            self.motionFirstrun = False

        if (self.status.motion.active_queue != stat.active_queue):
            self.status.motion.active_queue = stat.active_queue
            self.txStatus.motion.active_queue = stat.active_queue
            modified = True

        positionModified = False
        txPosition = None
        positionModified, txPosition = self.check_position(self.status.motion.actual_position, stat.actual_position)
        if positionModified:
            self.status.motion.actual_position.CopyFrom(txPosition)
            self.txStatus.motion.actual_position.MergeFrom(txPosition)
            modified = True

        if (self.status.motion.adaptive_feed_enabled != stat.adaptive_feed_enabled):
            self.status.motion.adaptive_feed_enabled = stat.adaptive_feed_enabled
            self.txStatus.motion.adaptive_feed_enabled = stat.adaptive_feed_enabled
            modified = True

        txAin = EmcStatusAnalogIO()
        for index, ain in enumerate(stat.ain):
            txAin.Clear()
            ainModified = False

            if len(self.status.motion.ain) == index:
                self.status.motion.ain.add()
                self.status.motion.ain[index].index = index
                self.status.motion.ain[index].value = 0.0

            if self.notEqual(self.status.motion.ain[index].value, ain):
                self.status.motion.ain[index].value = ain
                txAin.value = ain
                ainModified = True

            if ainModified:
                txAin.index = index
                self.txStatus.motion.ain.add().CopyFrom(txAin)
                modified = True
        del txAin

        txAout = EmcStatusAnalogIO()
        for index, aout in enumerate(stat.aout):
            txAout.Clear()
            aoutModified = False

            if len(self.status.motion.aout) == index:
                self.status.motion.aout.add()
                self.status.motion.aout[index].index = index
                self.status.motion.aout[index].value = 0.0

            if self.notEqual(self.status.motion.aout[index].value, aout):
                self.status.motion.aout[index].value = aout
                txAout.value = aout
                aoutModified = True

            if aoutModified:
                txAout.index = index
                self.txStatus.motion.aout.add().CopyFrom(txAout)
                modified = True
        del txAout

        txAxis = EmcStatusMotionAxis()
        for index, axis in enumerate(stat.axis):
            txAxis.Clear()
            axisModified = False

            if index == stat.axes:
                break

            if len(self.status.motion.axis) == index:
                self.status.motion.axis.add()
                self.status.motion.axis[index].index = index
                self.status.motion.axis[index].enabled = False
                self.status.motion.axis[index].fault = False
                self.status.motion.axis[index].ferror_current = 0.0
                self.status.motion.axis[index].ferror_highmark = 0.0
                self.status.motion.axis[index].homed = False
                self.status.motion.axis[index].homing = False
                self.status.motion.axis[index].inpos = False
                self.status.motion.axis[index].input = 0.0
                self.status.motion.axis[index].max_hard_limit = False
                self.status.motion.axis[index].max_soft_limit = False
                self.status.motion.axis[index].min_hard_limit = False
                self.status.motion.axis[index].min_soft_limit = False
                self.status.motion.axis[index].output = 0.0
                self.status.motion.axis[index].override_limits = False
                self.status.motion.axis[index].velocity = 0.0

            if self.status.motion.axis[index].enabled != axis['enabled']:
                self.status.motion.axis[index].enabled = axis['enabled']
                txAxis.enabled = axis['enabled']
                axisModified = True

            if self.status.motion.axis[index].fault != axis['fault']:
                self.status.motion.axis[index].fault = axis['fault']
                txAxis.fault = axis['fault']
                axisModified = True

            if self.notEqual(self.status.motion.axis[index].ferror_current, axis['ferror_current']):
                self.status.motion.axis[index].ferror_current = axis['ferror_current']
                txAxis.ferror_current = axis['ferror_current']
                axisModified = True

            if self.notEqual(self.status.motion.axis[index].ferror_highmark, axis['ferror_highmark']):
                self.status.motion.axis[index].ferror_highmark = axis['ferror_highmark']
                txAxis.ferror_highmark = axis['ferror_highmark']
                axisModified = True

            if self.status.motion.axis[index].homed != axis['homed']:
                self.status.motion.axis[index].homed = axis['homed']
                txAxis.homed = axis['homed']
                axisModified = True

            if self.status.motion.axis[index].homing != axis['homing']:
                self.status.motion.axis[index].homing = axis['homing']
                txAxis.homing = axis['homing']
                axisModified = True

            if self.status.motion.axis[index].inpos != axis['inpos']:
                self.status.motion.axis[index].inpos = axis['inpos']
                txAxis.inpos = axis['inpos']
                axisModified = True

            if self.notEqual(self.status.motion.axis[index].input, axis['input']):
                self.status.motion.axis[index].input = axis['input']
                txAxis.input = axis['input']
                axisModified = True

            if self.status.motion.axis[index].max_hard_limit != axis['max_hard_limit']:
                self.status.motion.axis[index].max_hard_limit = axis['max_hard_limit']
                txAxis.max_hard_limit = axis['max_hard_limit']
                axisModified = True

            if self.status.motion.axis[index].max_soft_limit != axis['max_soft_limit']:
                self.status.motion.axis[index].max_soft_limit = axis['max_soft_limit']
                txAxis.max_soft_limit = axis['max_soft_limit']
                axisModified = True

            if self.status.motion.axis[index].min_hard_limit != axis['min_hard_limit']:
                self.status.motion.axis[index].min_hard_limit = axis['min_hard_limit']
                txAxis.min_hard_limit = axis['min_hard_limit']
                axisModified = True

            if self.status.motion.axis[index].min_soft_limit != axis['min_soft_limit']:
                self.status.motion.axis[index].min_soft_limit = axis['min_soft_limit']
                txAxis.min_soft_limit = axis['min_soft_limit']
                axisModified = True

            if self.notEqual(self.status.motion.axis[index].output, axis['output']):
                self.status.motion.axis[index].output = axis['output']
                txAxis.output = axis['output']
                axisModified = True

            if self.status.motion.axis[index].override_limits != axis['override_limits']:
                self.status.motion.axis[index].override_limits = axis['override_limits']
                txAxis.override_limits = axis['override_limits']
                axisModified = True

            if self.notEqual(self.status.motion.axis[index].velocity, axis['velocity']):
                self.status.motion.axis[index].velocity = axis['velocity']
                txAxis.velocity = axis['velocity']
                axisModified = True

            if axisModified:
                txAxis.index = index
                self.txStatus.motion.axis.add().CopyFrom(txAxis)
                modified = True
        del txAxis

        if (self.status.motion.block_delete != stat.block_delete):
            self.status.motion.block_delete = stat.block_delete
            self.txStatus.motion.block_delete = stat.block_delete
            modified = True

        if (self.status.motion.current_line != stat.current_line):
            self.status.motion.current_line = stat.current_line
            self.txStatus.motion.current_line = stat.current_line
            modified = True

        if self.notEqual(self.status.motion.current_vel, stat.current_vel):
            self.status.motion.current_vel = stat.current_vel
            self.txStatus.motion.current_vel = stat.current_vel
            modified = True

        if self.notEqual(self.status.motion.delay_left, stat.delay_left):
            self.status.motion.delay_left = stat.delay_left
            self.txStatus.motion.delay_left = stat.delay_left
            modified = True

        txDin = EmcStatusDigitalIO()
        for index, din in enumerate(stat.din):
            txDin.Clear()
            dinModified = False

            if len(self.status.motion.din) == index:
                self.status.motion.din.add()
                self.status.motion.din[index].index = index
                self.status.motion.din[index].value = False

            if self.status.motion.din[index].value != din:
                self.status.motion.din[index].value = din
                txDin.value = din
                dinModified = True

            if dinModified:
                txDin.index = index
                self.txStatus.motion.din.add().CopyFrom(txDin)
                modified = True
        del txDin

        if self.notEqual(self.status.motion.distance_to_go, stat.distance_to_go):
            self.status.motion.distance_to_go = stat.distance_to_go
            self.txStatus.motion.distance_to_go = stat.distance_to_go
            modified = True

        txDout = EmcStatusDigitalIO()
        for index, dout in enumerate(stat.dout):
            txDout.Clear()
            doutModified = False

            if len(self.status.motion.dout) == index:
                self.status.motion.dout.add()
                self.status.motion.dout[index].index = index
                self.status.motion.dout[index].value = False

            if self.status.motion.dout[index].value != dout:
                self.status.motion.dout[index].value = dout
                txDout.value = dout
                doutModified = True

            if doutModified:
                txDout.index = index
                self.txStatus.motion.dout.add().CopyFrom(txDout)
                modified = True
        del txDout

        positionModified, txPosition = self.check_position(self.status.motion.dtg, stat.dtg)
        if positionModified:
            self.status.motion.dtg.CopyFrom(txPosition)
            self.txStatus.motion.dtg.MergeFrom(txPosition)
            modified = True

        if (self.status.motion.enabled != stat.enabled):
            self.status.motion.enabled = stat.enabled
            self.txStatus.motion.enabled = stat.enabled
            modified = True

        if (self.status.motion.feed_hold_enabled != stat.feed_hold_enabled):
            self.status.motion.feed_hold_enabled = stat.feed_hold_enabled
            self.txStatus.motion.feed_hold_enabled = stat.feed_hold_enabled
            modified = True

        if (self.status.motion.feed_override_enabled != stat.feed_override_enabled):
            self.status.motion.feed_override_enabled = stat.feed_override_enabled
            self.txStatus.motion.feed_override_enabled = stat.feed_override_enabled
            modified = True

        if self.notEqual(self.status.motion.feedrate, stat.feedrate):
            self.status.motion.feedrate = stat.feedrate
            self.txStatus.motion.feedrate = stat.feedrate
            modified = True

        if (self.status.motion.g5x_index != stat.g5x_index):
            self.status.motion.g5x_index = stat.g5x_index
            self.txStatus.motion.g5x_index = stat.g5x_index
            modified = True

        positionModified, txPosition = self.check_position(self.status.motion.g5x_offset, stat.g5x_offset)
        if positionModified:
            self.status.motion.g5x_offset.CopyFrom(txPosition)
            self.txStatus.motion.g5x_offset.MergeFrom(txPosition)
            modified = True

        positionModified, txPosition = self.check_position(self.status.motion.g92_offset, stat.g92_offset)
        if positionModified:
            self.status.motion.g92_offset.CopyFrom(txPosition)
            self.txStatus.motion.g92_offset.MergeFrom(txPosition)
            modified = True

        if (self.status.motion.id != stat.id):
            self.status.motion.id = stat.id
            self.txStatus.motion.id = stat.id
            modified = True

        if (self.status.motion.inpos != stat.inpos):
            self.status.motion.inpos = stat.inpos
            self.txStatus.motion.inpos = stat.inpos
            modified = True

        positionModified, txPosition = self.check_position(self.status.motion.joint_actual_position, stat.joint_actual_position)
        if positionModified:
            self.status.motion.joint_actual_position.CopyFrom(txPosition)
            self.txStatus.motion.joint_actual_position.MergeFrom(txPosition)
            modified = True

        positionModified, txPosition = self.check_position(self.status.motion.joint_position, stat.joint_position)
        if positionModified:
            self.status.motion.joint_position.CopyFrom(txPosition)
            self.txStatus.motion.joint_position.MergeFrom(txPosition)
            modified = True

        txLimit = EmcStatusLimit()
        for index, limit in enumerate(stat.limit):
            txLimit.Clear()
            limitModified = False

            if len(self.status.motion.limit) == index:
                self.status.motion.limit.add()
                self.status.motion.limit[index].index = index
                self.status.motion.limit[index].value = False

            if self.status.motion.limit[index].value != limit:
                self.status.motion.limit[index].value = limit
                txLimit.value = limit
                limitModified = True

            if limitModified:
                txLimit.index = index
                self.txStatus.motion.limit.add().CopyFrom(txLimit)
                modified = True
        del txLimit

        if (self.status.motion.motion_line != stat.motion_line):
            self.status.motion.motion_line = stat.motion_line
            self.txStatus.motion.motion_line = stat.motion_line
            modified = True

        if (self.status.motion.motion_type != stat.motion_type):
            self.status.motion.motion_type = stat.motion_type
            self.txStatus.motion.motion_type = stat.motion_type
            modified = True

        if (self.status.motion.motion_mode != stat.motion_mode):
            self.status.motion.motion_mode = stat.motion_mode
            self.txStatus.motion.motion_mode = stat.motion_mode
            modified = True

        if (self.status.motion.paused != stat.paused):
            self.status.motion.paused = stat.paused
            self.txStatus.motion.paused = stat.paused
            modified = True

        positionModified, txPosition = self.check_position(self.status.motion.position, stat.position)
        if positionModified:
            self.status.motion.position.CopyFrom(txPosition)
            self.txStatus.motion.position.MergeFrom(txPosition)
            modified = True

        if (self.status.motion.probe_tripped != stat.probe_tripped):
            self.status.motion.probe_tripped = stat.probe_tripped
            self.txStatus.motion.probe_tripped = stat.probe_tripped
            modified = True

        if (self.status.motion.probe_val != stat.probe_val):
            self.status.motion.probe_val = stat.probe_val
            self.txStatus.motion.probe_val = stat.probe_val
            modified = True

        positionModified, txPosition = self.check_position(self.status.motion.probed_position, stat.probed_position)
        if positionModified:
            self.status.motion.probed_position.CopyFrom(txPosition)
            self.txStatus.motion.probed_position.MergeFrom(txPosition)
            modified = True

        if (self.status.motion.probing != stat.probing):
            self.status.motion.probing = stat.probing
            self.txStatus.motion.probing = stat.probing
            modified = True

        if (self.status.motion.queue != stat.queue):
            self.status.motion.queue = stat.queue
            self.txStatus.motion.queue = stat.queue
            modified = True

        if (self.status.motion.queue_full != stat.queue_full):
            self.status.motion.queue_full = stat.queue_full
            self.txStatus.motion.queue_full = stat.queue_full
            modified = True

        if self.notEqual(self.status.motion.rotation_xy, stat.rotation_xy):
            self.status.motion.rotation_xy = stat.rotation_xy
            self.txStatus.motion.rotation_xy = stat.rotation_xy
            modified = True

        if (self.status.motion.spindle_brake != stat.spindle_brake):
            self.status.motion.spindle_brake = stat.spindle_brake
            self.txStatus.motion.spindle_brake = stat.spindle_brake
            modified = True

        if (self.status.motion.spindle_direction != stat.spindle_direction):
            self.status.motion.spindle_direction = stat.spindle_direction
            self.txStatus.motion.spindle_direction = stat.spindle_direction
            modified = True

        if (self.status.motion.spindle_enabled != stat.spindle_enabled):
            self.status.motion.spindle_enabled = stat.spindle_enabled
            self.txStatus.motion.spindle_enabled = stat.spindle_enabled
            modified = True

        if (self.status.motion.spindle_increasing != stat.spindle_increasing):
            self.status.motion.spindle_increasing = stat.spindle_increasing
            self.txStatus.motion.spindle_increasing = stat.spindle_increasing
            modified = True

        if (self.status.motion.spindle_override_enabled != stat.spindle_override_enabled):
            self.status.motion.spindle_override_enabled = stat.spindle_override_enabled
            self.txStatus.motion.spindle_override_enabled = stat.spindle_override_enabled
            modified = True

        if self.notEqual(self.status.motion.spindle_speed, stat.spindle_speed):
            self.status.motion.spindle_speed = stat.spindle_speed
            self.txStatus.motion.spindle_speed = stat.spindle_speed
            modified = True

        if self.notEqual(self.status.motion.spindlerate, stat.spindlerate):
            self.status.motion.spindlerate = stat.spindlerate
            self.txStatus.motion.spindlerate = stat.spindlerate
            modified = True

        if (self.status.motion.state != stat.state):
            self.status.motion.state = stat.state
            self.txStatus.motion.state = stat.state
            modified = True

        if self.motionFullUpdate:
            self.add_pparams()
            self.send_motion(self.status.motion, MT_EMCSTAT_FULL_UPDATE)
            self.motionFullUpdate = False
        elif modified:
            self.send_motion(self.txStatus.motion, MT_EMCSTAT_INCREMENTAL_UPDATE)

    def update_status(self, stat):
        self.txStatus.clear()
        if (self.ioSubscriptions > 0):
            self.update_io(stat)
        if (self.taskSubscriptions > 0):
            self.update_task(stat)
        if (self.interpSubscriptions > 0):
            self.update_interp(stat)
        if (self.motionSubscriptions > 0):
            self.update_motion(stat)
        if (self.configSubscriptions > 0):
            self.update_config(stat)

    def update_error(self, error):
        if not error:
            return

        kind, text = error
        self.tx.note.append(text)

        if (kind == linuxcnc.NML_ERROR):
            if (self.errorSubscriptions > 0):
                self.send_error_msg('error', MT_EMC_NML_ERROR)
        elif (kind == linuxcnc.OPERATOR_ERROR):
            if (self.errorSubscriptions > 0):
                self.send_error_msg('error', MT_EMC_OPERATOR_ERROR)
        elif (kind == linuxcnc.NML_TEXT):
            if (self.textSubscriptions > 0):
                self.send_error_msg('text', MT_EMC_NML_TEXT)
        elif (kind == linuxcnc.OPERATOR_TEXT):
            if (self.textSubscriptions > 0):
                self.send_error_msg('text', MT_EMC_OPERATOR_TEXT)
        elif (kind == linuxcnc.NML_DISPLAY):
            if (self.displaySubscriptions > 0):
                self.send_error_msg('display', MT_EMC_NML_DISPLAY)
        elif (kind == linuxcnc.OPERATOR_DISPLAY):
            if (self.displaySubscriptions > 0):
                self.send_error_msg('display', MT_EMC_OPERATOR_DISPLAY)

    def send_config(self, data, type):
        self.tx.emc_status_config.MergeFrom(data)
        if self.debug:
            print("sending config message")
        self.send_status_msg('config', type)

    def send_io(self, data, type):
        self.tx.emc_status_io.MergeFrom(data)
        if self.debug:
            print("sending io message")
        self.send_status_msg('io', type)

    def send_task(self, data, type):
        self.tx.emc_status_task.MergeFrom(data)
        if self.debug:
            print("sending task message")
        self.send_status_msg('task', type)

    def send_motion(self, data, type):
        self.tx.emc_status_motion.MergeFrom(data)
        if self.debug:
            print("sending motion message")
        self.send_status_msg('motion', type)

    def send_interp(self, data, type):
        self.tx.emc_status_interp.MergeFrom(data)
        if self.debug:
            print("sending interp message")
        self.send_status_msg('interp', type)

    def send_status_msg(self, topic, type):
        self.tx.type = type
        txBuffer = self.tx.SerializeToString()
        self.tx.Clear()
        self.statusSocket.send_multipart([topic, txBuffer])

    def send_error_msg(self, topic, type):
        self.tx.type = type
        txBuffer = self.tx.SerializeToString()
        self.tx.Clear()
        self.errorSocket.send_multipart([topic, txBuffer])

    def send_command_msg(self, type):
        self.tx.type = type
        txBuffer = self.tx.SerializeToString()
        self.tx.Clear()
        self.commandSocket.send(txBuffer)

    def add_pparams(self):
        parameters = ProtocolParameters()
        parameters.keepalive_timer = self.pingInterval * 1000
        self.tx.pparams.MergeFrom(parameters)

    def poll(self):
        while True:
            try:
                if (self.totalSubscriptions > 0):
                    self.stat.poll()
                    self.update_status(self.stat)
                    if (self.pingCount == self.pingRatio):
                        self.ping_status()

                if (self.totalErrorSubscriptions > 0):
                    error = self.error.poll()
                    self.update_error(error)
                    if (self.pingCount == self.pingRatio):
                        self.ping_error()

            except linuxcnc.error as detail:
                print(("error", detail))

            if (self.pingCount == self.pingRatio):
                self.pingCount = 0
            else:
                self.pingCount += 1
            time.sleep(self.pollInterval)

    def ping_status(self):
        if (self.ioSubscriptions > 0):
            self.send_status_msg('io', MT_PING)
        if (self.taskSubscriptions > 0):
            self.send_status_msg('task', MT_PING)
        if (self.interpSubscriptions > 0):
            self.send_status_msg('interp', MT_PING)
        if (self.motionSubscriptions > 0):
            self.send_status_msg('motion', MT_PING)
        if (self.configSubscriptions > 0):
            self.send_status_msg('config', MT_PING)

    def ping_error(self):
        if self.newErrorSubscription:        # not very clear
            self.add_pparams()
            self.newErrorSubscription = False

        if (self.errorSubscriptions > 0):
            self.send_error_msg('error', MT_PING)
        if (self.textSubscriptions > 0):
            self.send_error_msg('text', MT_PING)
        if (self.displaySubscriptions > 0):
            self.send_error_msg('display', MT_PING)

    def process_status(self, socket):
        try:
            rc = socket.recv(zmq.NOBLOCK)
            subscription = rc[1:]
            status = (rc[0] == "\x01")

            if subscription == 'motion':
                if status:
                    self.motionSubscriptions += 1
                    self.motionFullUpdate = True
                else:
                    self.motionSubscriptions -= 1
            elif subscription == 'task':
                if status:
                    self.taskSubscriptions += 1
                    self.taskFullUpdate = True
                else:
                    self.taskSubscriptions -= 1
            elif subscription == 'io':
                if status:
                    self.ioSubscriptions += 1
                    self.ioFullUpdate = True
                else:
                    self.ioSubscriptions -= 1
            elif subscription == 'config':
                if status:
                    self.configSubscriptions += 1
                    self.configFullUpdate = True
                else:
                    self.configSubscriptions -= 1
            elif subscription == 'interp':
                if status:
                    self.interpSubscriptions += 1
                    self.interpFullUpdate = True
                else:
                    self.interpSubscriptions -= 1

            self.totalSubscriptions = self.motionSubscriptions \
            + self.taskSubscriptions \
            + self.ioSubscriptions \
            + self.configSubscriptions \
            + self.interpSubscriptions

            print(("process status called " + subscription + ' ' + str(status)))
            print(("total status subscriptions: " + str(self.totalSubscriptions)))

        except zmq.ZMQError:
            print("ZMQ error")

    def process_error(self, socket):
        try:
            rc = socket.recv(zmq.NOBLOCK)
            subscription = rc[1:]
            status = (rc[0] == "\x01")

            if subscription == 'error':
                if status:
                    self.newErrorSubscription = True
                    self.errorSubscriptions += 1
                else:
                    self.errorSubscriptions -= 1
            elif subscription == 'text':
                if status:
                    self.newErrorSubscription = True
                    self.textSubscriptions += 1
                else:
                    self.textSubscriptions -= 1
            elif subscription == 'display':
                if status:
                    self.newErrorSubscription = True
                    self.displaySubscriptions += 1
                else:
                    self.displaySubscriptions -= 1

            self.totalErrorSubscriptions = self.errorSubscriptions \
            + self.textSubscriptions \
            + self.displaySubscriptions

            print(("process error called " + subscription + ' ' + str(status)))
            print(("total error subscriptions: " + str(self.totalErrorSubscriptions)))

        except zmq.ZMQError:
            print("ZMQ error")

    def send_command_wrong_params(self):
        self.tx.note.append("wrong parameters")
        self.send_command_msg(MT_ERROR)

    def process_command(self, socket):
        print("process command called")

        message = socket.recv()
        self.rx.ParseFromString(message)

        if self.rx.type == MT_PING:
            self.send_command_msg(MT_PING_ACKNOWLEDGE)

        elif self.rx.type == MT_EMC_TASK_ABORT:
            self.command.abort()

        elif self.rx.type == MT_EMC_TASK_PLAN_PAUSE:
            self.command.auto(linuxcnc.AUTO_PAUSE)

        elif self.rx.type == MT_EMC_TASK_PLAN_RESUME:
            self.command.auto(linuxcnc.AUTO_RESUME)

        elif self.rx.type == MT_EMC_TASK_PLAN_STEP:
            self.command.auto(linuxcnc.AUTO_STEP)

        elif self.rx.type == MT_EMC_TASK_PLAN_RUN:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('line_number'):
                lineNumber = self.rx.emc_command_params.line_number
                self.command.auto(linuxcnc.AUTO_RUN, lineNumber)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_SPINDLE_BRAKE_ENGAGE:
            self.command.brake(linuxcnc.BRAKE_ENGAGE)

        elif self.rx.type == MT_EMC_SPINDLE_BRAKE_RELEASE:
            self.command.brake(linuxcnc.BRAKE_RELEASE)

        elif self.rx.type == MT_EMC_SET_DEBUG:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('debug_level'):
                debugLevel = self.rx.emc_command_params.debug_level
                self.command.debug(debugLevel)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TRAJ_SET_SCALE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('scale'):
                feedrate = self.rx.emc_command_params.scale
                self.command.feedrate(feedrate)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_COOLANT_FLOOD_ON:
            self.command.flood(linuxcnc.FLOOD_ON)

        elif self.rx.type == MT_EMC_COOLANT_FLOOD_OFF:
            self.command.flood(linuxcnc.FLOOD_OFF)

        elif self.rx.type == MT_EMC_AXIS_HOME:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index'):
                axis = self.rx.emc_command_params.index
                self.command.home(axis)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_AXIS_ABORT:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index'):
                axis = self.rx.emc_command_params.index
                self.command.jog(linuxcnc.JOG_STOP, axis)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_AXIS_JOG:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index') \
            and self.rx.emc_command_params.HasField('velocity'):
                axis = self.rx.emc_command_params.index
                velocity = self.rx.emc_command_params.velocity
                self.command.jog(linuxcnc.JOG_CONTINUOUS, axis, velocity)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_AXIS_INCR_JOG:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index') \
            and self.rx.emc_command_params.HasField('velocity') \
            and self.rx.emc_command_params.HasField('distance'):
                axis = self.rx.emc_command_params.index
                velocity = self.rx.emc_command_params.velocity
                distance = self.rx.emc_command_params.distance
                self.command.jog(linuxcnc.JOG_INCREMENT, axis, velocity, distance)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TOOL_LOAD_TOOL_TABLE:
            self.command.load_tool_table()

        elif self.rx.type == MT_EMC_TRAJ_SET_MAX_VELOCITY:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('velocity'):
                velocity = self.rx.emc_command_params.velocity
                self.command.maxvel(velocity)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TASK_PLAN_EXECUTE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('command'):
                command = self.rx.emc_command_params.command
                self.command.mdi(command)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_COOLANT_MIST_ON:
            self.command.mist(linuxcnc.MIST_ON)

        elif self.rx.type == MT_EMC_COOLANT_MIST_OFF:
            self.command.mist(linuxcnc.MIST_OFF)

        elif self.rx.type == MT_EMC_TASK_SET_MODE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('task_mode'):
                self.command.mode(self.rx.emc_command_params.task_mode)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_AXIS_OVERRIDE_LIMITS:
            self.command.override_limits()

        elif self.rx.type == MT_EMC_TASK_PLAN_OPEN:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('path'):
                fileName = self.rx.emc_command_params.path
                self.command.program_open(os.path.join(self.directory, fileName))
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TASK_PLAN_INIT:
            self.command.reset_interpreter()

        elif self.rx.type == MT_EMC_MOTION_ADAPTIVE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('enable'):
                adaptiveFeed = self.rx.emc_command_params.enable
                self.command.set_adaptive_feed(adaptiveFeed)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_MOTION_SET_AOUT:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index') \
            and self.rx.emc_command_params.HasField('value'):
                axis = self.rx.emc_command_params.index
                value = self.rx.emc_command_params.value
                self.command.set_analog_output(axis, value)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TASK_PLAN_SET_BLOCK_DELETE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('enable'):
                blockDelete = self.rx.emc_command_params.enable
                self.command.set_block_delete(blockDelete)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_MOTION_SET_DOUT:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index') \
            and self.rx.emc_command_params.HasField('enable'):
                axis = self.rx.emc_command_params.index
                value = self.rx.emc_command_params.enable
                self.command.set_digital_output(axis, value)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TRAJ_SET_FH_ENABLE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('enable'):
                feedHold = self.rx.emc_command_params.enable
                self.command.set_feed_hold(feedHold)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TRAJ_SET_FO_ENABLE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('enable'):
                feedOverride = self.rx.emc_command_params.enable
                self.command.set_feed_override(feedOverride)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_AXIS_SET_MAX_POSITION_LIMIT:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index') \
            and self.rx.emc_command_params.HasField('value'):
                axis = self.rx.emc_command_params.index
                value = self.rx.emc_command_params.value
                self.command.set_max_limit(axis, value)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_AXIS_SET_MIN_POSITION_LIMIT:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index') \
            and self.rx.emc_command_params.HasField('value'):
                axis = self.rx.emc_command_params.index
                value = self.rx.emc_command_params.value
                self.command.set_min_limit(axis, value)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TASK_PLAN_SET_OPTIONAL_STOP:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('enable'):
                optionalStop = self.rx.emc_command_params.enable
                self.command.set_optional_stop(optionalStop)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TRAJ_SET_SO_ENABLE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('enable'):
                spindleOverride = self.rx.emc_command_params.enable
                self.command.set_spindle_override(spindleOverride)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_SPINDLE_ON:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('velocity'):
                speed = self.rx.emc_command_params.velocity
                direction = linuxcnc.SPINDLE_FORWARD    # always forwward, speed can be signed
                self.command.spindle(direction, speed)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_SPINDLE_INCREASE:
            self.command.spindle(linuxcnc.SPINDLE_INCREASE)

        elif self.rx.type == MT_EMC_SPINDLE_DECREASE:
            self.command.spindle(linuxcnc.SPINDLE_DECREASE)

        elif self.rx.type == MT_EMC_SPINDLE_CONSTANT:
            self.command.spindle(linuxcnc.SPINDLE_CONSTANT)

        elif self.rx.type == MT_EMC_SPINDLE_OFF:
            self.command.spindle(linuxcnc.SPINDLE_OFF)

        elif self.rx.type == MT_EMC_TRAJ_SET_SPINDLE_SCALE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('scale'):
                scale = self.rx.emc_command_params.scale
                self.command.spindleoverride(scale)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TASK_SET_STATE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('task_state'):
                self.command.state(self.rx.emc_command_params.task_state)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TRAJ_SET_TELEOP_ENABLE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('enable'):
                teleopEnable = self.rx.emc_command_params.enable
                self.command.teleop_enable(teleopEnable)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TRAJ_SET_TELEOP_VECTOR:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('pose') \
            and self.rx.emc_command_params.pose.HasField('a') \
            and self.rx.emc_command_params.pose.HasField('b') \
            and self.rx.emc_command_params.pose.HasField('c'):
                a = self.rx.emc_command_params.pose.a
                b = self.rx.emc_command_params.pose.b
                c = self.rx.emc_command_params.pose.c
                if self.rx.emc_command_params.pose.HasField('u'):
                    u = self.rx.emc_command_params.pose.u
                    if self.rx.emc_command_params.pose.HasField('v'):
                        v = self.rx.emc_command_params.pose.v
                        if self.rx.emc_command_params.pose.HasField('w'):
                            w = self.rx.emc_command_params.pose.w
                            self.command.teleop_vector(a, b, c, u, v, w)
                        else:
                            self.command.teleop_vector(a, b, c, u, v)
                    else:
                        self.command.teleop_vector(a, b, c, u)
                else:
                    self.command.teleop_vector(a, b, c)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TOOL_SET_OFFSET:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('tool_data') \
            and self.rx.emc_command_params.tool_data.index \
            and self.rx.emc_command_params.tool_data.zOffset \
            and self.rx.emc_command_params.tool_data.xOffset \
            and self.rx.emc_command_params.tool_data.diameter \
            and self.rx.emc_command_params.tool_data.frontangle \
            and self.rx.emc_command_params.tool_data.backangle \
            and self.rx.emc_command_params.tool_data.orientation:
                toolno = self.rx.emc_command_params.tool_data.index
                z_offset = self.rx.emc_command_params.tool_data.zOffset
                x_offset = self.rx.emc_command_params.tool_data.xOffset
                diameter = self.rx.emc_command_params.tool_data.diameter
                frontangle = self.rx.emc_command_params.tool_data.frontangle
                backangle = self.rx.emc_command_params.tool_data.backangle
                orientation = self.rx.emc_command_params.tool_data.orientation
                self.command.tool_offset(toolno, z_offset, x_offset, diameter,
                    frontangle, backangle, orientation)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_TRAJ_SET_MODE:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('traj_mode'):
                self.command.traj_mode(self.rx.emc_command_params.traj_mode)
            else:
                self.send_command_wrong_params()

        elif self.rx.type == MT_EMC_AXIS_UNHOME:
            if self.rx.HasField('emc_command_params') \
            and self.rx.emc_command_params.HasField('index'):
                axis = self.rx.emc_command_params.index
                self.command.unhome(axis)
            else:
                self.send_command_wrong_params()

        else:
            self.tx.note.append("unknown command")
            self.send_command_msg(MT_ERROR)


def choose_ip(pref):
    '''
    given an interface preference list, return a tuple (interface, IPv4)
    or None if no match found
    If an interface has several IPv4 addresses, the first one is picked.
    pref is a list of interface names or prefixes:

    pref = ['eth0','usb3']
    or
    pref = ['wlan','eth', 'usb']
    '''

    # retrieve list of network interfaces
    interfaces = netifaces.interfaces()

    # find a match in preference oder
    for p in pref:
        for i in interfaces:
            if i.startswith(p):
                ifcfg = netifaces.ifaddresses(i)
                # we want the first IPv4 address
                try:
                    ip = ifcfg[netifaces.AF_INET][0]['addr']
                except KeyError:
                    continue
                return (i, ip)
    return None


def main():
    debug = True

    if (len(sys.argv) > 1):
        iniFile = sys.argv[1]
    else:
        iniFile = ""

    mkini = os.getenv("MACHINEKIT_INI")
    if mkini is None:
        sys.stderr.write("no MACHINEKIT_INI environemnt variable set")
        sys.exit(1)

    mki = ConfigParser.ConfigParser()
    mki.read(mkini)
    uuid = mki.get("MACHINEKIT", "MKUUID")
    remote = mki.getint("MACHINEKIT", "REMOTE")
    prefs = mki.get("MACHINEKIT", "INTERFACES").split()

    if remote == 0:
        print("Remote communication is deactivated, linuxcncwrap will not start")
        print(("set REMOTE in " + mkini + " to 1 to enable remote communication"))
        sys.exit(0)

    iface = choose_ip(prefs)
    if not iface:
        sys.stderr.write("failed to determine preferred interface (preference = %s)" % prefs)
        sys.exit(1)

    if debug:
        print(("announcing linuxcncwrap on " + str(iface)))

    context = zmq.Context()
    context.linger = 0

    uri = "tcp://" + iface[0]

    fileService = FileService(iniFile=iniFile, svcUuid=uuid, ipv4=iface[1], debug=debug)

    wrapper = LinuxCNCWrapper(context, uri, uri, uri,
                              iniFile=iniFile,
                              svcUuid=uuid,
                              ipv4=iface[1],
                              debug=debug)

if __name__ == "__main__":
    main()