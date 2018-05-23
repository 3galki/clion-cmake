import os
import subprocess
import sys
from cmake_misc.remote import Remote


def get_ssh_shell(server):
    sys.stderr.write("Build on server: %s\n" % server)
    port = server.rfind(':')
    if port is None:
        return Ssh(host=server)
    else:
        return Ssh(host=server[0:port], params='ssh -p ' + server[port + 1:])


class Ssh(Remote):
    def __init__(self, host, params=''):
        super().__init__(host, params)

    def sync_artifacts(self, path):
        sys.stderr.write("Get artifacts: %s\n" % path)
        if path[-1] == '/':
            subprocess.call(['rsync', '-a', '-e', self._params, self._host + ':' + path, path[0:-1]])
        else:
            subprocess.call(['scp'] + self._prepare_args([self._host + ':' + path, path])[1:])

    def call(self, args, desc=None, **kwargs):
        self.desc(args, desc)
        return subprocess.call(args=self._prepare_args(args), **kwargs)

    def run(self, args):
        sys.stderr.write("Execute: %s\n" % ' '.join(self._prepare_args(args)))
        return subprocess.run(args=self._prepare_args(args), stdout=subprocess.PIPE).stdout.decode().strip()

    def _prepare_args(self, args):
        return self._params.split(' ') + [self._host, '--'] + args

    def mkdir(self, dir):
        if self.call(['mkdir', '-p', dir], desc="Make folder: %s" % dir) != 0:
            exit("Failed to connect to build server")
