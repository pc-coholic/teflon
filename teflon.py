#!/usr/bin/env python
from configobj import ConfigObj
from subprocess import Popen,PIPE
from socket import *
from select import select
from thread import start_new_thread
from time import sleep
from os import environ
import sys

class IProxySystemApp:
  def LaunchProxy(self, localPort, remoteHost, remotePort, sshIdentity):
    pass
  def ShutdownProxy(self):
    pass

class SshApp(IProxySystemApp):
  def LaunchProxy(self, localPort, remoteHost, remotePort, sshIdentity):
    cmdlist = ["ssh", "-N"]
    cmdlist += ["-D", str(localPort)]
    if(remotePort):
      cmdlist += [ "-p", str(remotePort)]
    if(sshIdentity):
      cmdlist += ["-i", sshIdentity]
    cmdlist += [remoteHost]
    self.proxyProc = Popen(cmdlist, stdin=PIPE, stdout=PIPE)

class AutoSshApp(IProxySystemApp):
  def LaunchProxy(self, localPort, remoteHost, remotePort, sshIdentity):
    env = {}
    for envKey in environ:
      env[envKey] = environ[envKey]
    env["AUTOSSH_MAXSTART"] = "1"
    env["AUTOSSH_POLL"] = "20"
    cmdlist = ["autossh", "-M", str(localPort+1)]
    cmdlist += ["-D", str(localPort)]
    if(remotePort):
      cmdlist += [ "-p", str(remotePort)]
    if(sshIdentity):
      cmdlist += ["-i", sshIdentity]
    cmdlist += ["-N"]
    cmdlist += [remoteHost]
    #print cmdlist
    self.proxyProc = Popen(cmdlist, stdin=PIPE, stdout=PIPE, env=env)

class Proxy:
  maxBacklog = 50
  networkTimeout = 1.0
  startupTimeout = 2
  def __init__(self, remoteHost, localSocksPort,
               remotePort=None, localSshProxyPort=None, sshIdentity=None,
               app=AutoSshApp()):
    self.remoteHost = remoteHost
    self.remotePort = remotePort
    self.localSocksPort = int(localSocksPort)
    if not localSshProxyPort:
      localSshProxyPort = self.localSocksPort + 1
    self.localSshProxyPort = localSshProxyPort
    self.sshIdentity = sshIdentity
    self.systemApp = app
    self.fds = []
    self.l2p = {}
    self.f2s = {}

  def Start(self):
    self.StartListening()
    self.threadId = start_new_thread(Proxy.WorkLoop, (self,) )

  def StartListening(self):
    """Start listening for client connections on <localSocksPort>"""
    self.listener = socket()
    self.listener.bind( ("127.0.0.1", int(self.localSocksPort)) )
    self.listener.listen(self.maxBacklog)
    self.fds.append(self.listener.fileno())

  def HandleNew(self):
    (newsock, remoteaddr) = self.listener.accept()
    if not self.IsSshProxyUp():
      self.Flush()
      self.StartSshProxy()
    newproxy = socket()
    newproxy.connect( ("127.0.0.1", int(self.localSshProxyPort)) )
    nsfd = newsock.fileno()
    npfd = newproxy.fileno()
    self.l2p[nsfd] = npfd
    self.l2p[npfd] = nsfd
    self.f2s[nsfd] = newsock
    self.f2s[npfd] = newproxy
    self.fds.append(nsfd)
    self.fds.append(npfd)

  def WorkLoop(self):
    while(True):
      self.Work()

  def Work(self):
    (readme, none, none) = select(self.fds, [], [], self.networkTimeout)
    listenfd = self.listener.fileno()
    for fd in readme:
      if fd is not listenfd:
        self.Shuttle(fd)
      else:
        try:
          self.HandleNew()
        except:
          print "Failed to start proxy application"

  def Shuttle(self, readyFd):
    try:
      inSock = self.f2s[readyFd]
      outSock = self.f2s[self.l2p[readyFd]]
      data = inSock.recv(1024)
      if len(data) == 0:
        self.Teardown(inSock, outSock)
      else:
        outSock.send(data)
    except (KeyError):
      # we got here if a previous fd returned by select was our partner
      # and the link was torn down then
      pass
    except error:
      print "Notice: recv()/send() exception caught. Removing bad connection"
      self.Teardown(inSock, outSock)

  def Teardown(self, sockA, sockB):
    fda = sockA.fileno()
    fdb = sockB.fileno()
    self.fds.remove(fda)
    self.fds.remove(fdb)
    del self.l2p[fda]
    del self.l2p[fdb]
    del self.f2s[fda]
    del self.f2s[fdb]
    sockA.close()
    sockB.close()

  def Flush(self):
    while len(self.fds) > 1:
      candidate_socket = self.f2s[self.fds[1]]
      partner_socket = self.f2s[self.l2p[self.fds[1]]]
      Teardown(candidate_socket, partner_socket) 

  def IsSshProxyUp(self):
    test_sock = socket()
    bind_up = True
    try:
      test_sock.connect( ("127.0.0.1", self.localSshProxyPort) )
    except error:
      bind_up = False
    test_sock.close()
    return bind_up

  def StartSshProxy(self):
    print "Starting proxy via host", self.remoteHost
    self.systemApp.LaunchProxy(self.localSshProxyPort, self.remoteHost,
                               self.remotePort, self.sshIdentity)

    # Wait until either the proxy has started, or we've reached the
    # startup timeout. On timeout, throw an exception
    timer = 0
    while not self.IsSshProxyUp():
      timer += 0.001
      sleep(0.001)
      if timer > self.startupTimeout:
        print "Timeout waiting for proxy application to startup"
        raise Exception("hell")


def main():
  proxies = []

  if len(sys.argv) == 3:
    if sys.argv[1] == '-c':
      configfile = sys.argv[2]
    else:
      print "Useage: teflon.py [-c <configfile>]"
      return
  elif len(sys.argv) == 1:
    configfile = "teflon.conf"
  else:
    print "Useage: teflon.py [-c <configfile>]"
    return

  config = ConfigObj(configfile)
  for section in config.itervalues():
    remoteHost = section['remote_host']
    localSocksPort = section['local_socks_port']

    sshIdentity = None
    if section.has_key('ssh_identity'):
      sshIdentity = section['ssh_identity']

    program = AutoSshApp()
    if section.has_key('proxy_application'):
      app_string = section['proxy_application']
      if app_string == 'ssh':
        program = SshApp()
      elif app_string == 'autossh':
        program = AutoSshApp()

    proxies.append(Proxy(remoteHost, localSocksPort, sshIdentity=sshIdentity,
                         app=program))

  map(Proxy.Start, proxies)

  if len(proxies) == 0:
    print "No proxies configured. Quitting"
    return

  while (True):
    sleep(100)

main()
