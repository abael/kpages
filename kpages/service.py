# -*- coding:utf-8 -*-

"""
    Reids MQ Service



    作者: Q.yuhen
    创建: 2011-08-01

    历史:
        2011-08-03  + srvcmd 装饰器
        2011-08-04  * 取消 Service callback，改为自动查找 service function。
                    + 增加 _send_pack, _unpack。
        2011-08-07  * 将 Consumer 从 Service 中分离。便于单元测试使用。
        2011-08-27  + 新增 service_async 函数。
        2011-08-28  * 重构 Pack 为一个独立类。
        2011-08-29  * 取消 Timeout。
"""
import datetime,json
from sys import stderr, argv
from multiprocessing import cpu_count,Process
try:
    from os import wait, fork, getpid, getppid, killpg, waitpid
    from signal import signal, pause, SIGCHLD, SIGINT, SIGTERM, SIGUSR1, SIGUSR2, SIG_IGN
    iswin = False
except:
    iswin = True
    print 'some function only support unix and linux '

from redis import Redis, ConnectionError
from json import loads, dumps

from context import LogicContext, get_context
from utility import get_members


def staticclass(cls):
    def new(cls, *args, **kwargs):
        raise RuntimeError("Static Class")

    setattr(cls, "__new__", staticmethod(new))
    return cls


def srvcmd(cmd):
    def actual(func):
        func.__service__ = cmd
        return func

    return actual

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        
        return json.JSONEncoder.default(self, obj)
            

@staticclass
class Pack(object):

    @staticmethod
    def send_pack(mq, channel, cmd, data):
        pack = dumps(dict(cmd=cmd, data=data),cls=DateTimeEncoder)
        mq.publish(channel, pack)

    @staticmethod
    def unpack(data):
        try:
            pack = loads(data)
            return pack["cmd"], pack["data"]
        except:
            return 'undefined cmd', None

    @classmethod
    def async_send_pack(cls, cmd, data, channel=None):
        cls.send_pack(get_context(
        ).get_redis(), channel or __conf__.SERVICE_CHANNEL, cmd, data)


class Consumer(object):
    def __init__(self, channel, host="localhost"):
        self._channel = channel
        self._host = host

    def subscribe(self):
        self._redis = Redis(host=self._host) 
        self._pubsub = self._redis.pubsub()
        self._pubsub.subscribe(self._channel)
        self._listen = None

    def consume(self):
        if not self._listen:
            self._listen = self._pubsub.listen()

        return Pack.unpack(self._listen.next()["data"])

    def close(self):
        if hasattr(self, "_redis"):
            self._redis.connection_pool.disconnect()


class Service(object):
    """
        MQ Service

        Demo:
            ./service.py [host] [channel]

            host    default "localhost"
            channel defailt  settings.py SERVICE_CHANNEL
    """
    def __init__(self, host=None, channel=None, callback=None):
        self._host = host or "localhost"
        self._channel = channel or __conf__.SERVICE_CHANNEL
        #self._processes = __conf__.DEBUG and 1 or cpu_count()
        self._processes = 1

        self._consumer = Consumer(self._channel, self._host)
        self._services = self._get_services()

        if callback:
            kwargs = dict(
                host=self._host,
                channel=self._channel,
                processes=self._processes,
                services=self._services,
                debug=__conf__.DEBUG
            )
            callback(**kwargs)

    def _signal(self):

        self._parent = getpid()
        def sig_handler(signum, frame):
            pid = getpid()

            if signum in (SIGINT, SIGTERM):
                if pid == self._parent:
                    signal(SIGCHLD, SIG_IGN)
                    killpg(self._parent, SIGUSR1)
            elif signum == SIGCHLD:
                if pid == self._parent:
                    print >> stderr, "sub process {0} exit...".format(wait())
            elif signum == SIGUSR1:
                print "process {0} exit...".format(pid == self._parent and pid or (pid, self._parent))
                exit(0)

        signal(SIGINT, sig_handler)
        signal(SIGTERM, sig_handler)
        signal(SIGCHLD, sig_handler)
        signal(SIGUSR1, sig_handler)

    def _get_services(self):
        try:
            members = get_members(
                __conf__.JOB_DIR, lambda o: hasattr(o, "__service__"))
            svrs = {}
            for k,v in members.items():
                if not svrs.get(v.__service__,None):
                    svrs[v.__service__] = []
                
                svrs[v.__service__].append(v)
                
            #return dict([(v.__service__, v) for k, v in members.items()])
            return svrs

        except Exception as e:
            print 'load error:',e,e.message 
            return {}

    def _subscribe(self):
        self._consumer.subscribe()

        for i in range(self._processes):
            if not iswin:
                if fork() > 0:
                    continue

            try:
                with LogicContext():
                    while True:
                        cmd, data = self._consumer.consume()
                        srv_funcs = self._services.get(cmd,())
                        for fun in srv_funcs:
                            try:
                                p = Process(target=fun,args=(data,))
                                p.start()
                                p.join()
                            except Exception as e:
                                print cmd,e

            except ConnectionError as e:
                print 'Expception:'+e.message

            exit(0)

    def run(self):
        if not iswin:
            self._signal()

        try:
            self._subscribe()
        except RuntimeError:
            print "Is running?"
            exit(-1)

        while True:
            pause()


Redis.send_pack = Pack.send_pack
service_async = Pack.async_send_pack


__all__ = ["Consumer", "Service", "srvcmd", "service_async"]
