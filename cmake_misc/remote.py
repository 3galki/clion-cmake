import os
import subprocess
import sys


class Remote:
    def __init__(self, host, params=''):
        self._params = params
        self._host = host

    def sync_source(self, source_path):
        self.mkdir(source_path)
        sys.stderr.write("Sync source: %s\n" % source_path)
        command = ['rsync', '-a', '-v', '-e', self._params, '--delete',
                   '--exclude=/cmake-build-*', '--exclude=/.idea', '--exclude=.git']
        gitignore = os.path.join(source_path, '.gitignore')
        if os.path.isfile(gitignore):
            command.append('--exclude-from=' + gitignore)
        command.append(source_path + '/')
        command.append(self._host + ':' + source_path)
        subprocess.run(command)

    def desc(self, args, desc):
        if desc is None:
            sys.stderr.write("Execute: %s\n" % ' '.join(self._prepare_args(args)))
        elif len(desc) > 0:
            sys.stderr.write("%s\n" % desc)

    def _prepare_args(self, args):
        return args
