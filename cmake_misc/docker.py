import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from cmake_misc.remote import Remote

_docker_script = '''FROM lasote/conanclang60
LABEL maintainer "greed@ispsystem.com"

COPY libpopt0_1.16-11_amd64.deb /root/libpopt0_1.16-11_amd64.deb
COPY rsync_3.1.2-2.1_amd64.deb /root/rsync_3.1.2-2.1_amd64.deb
RUN sudo dpkg -i /root/libpopt0_1.16-11_amd64.deb &&\
 sudo dpkg -i /root/rsync_3.1.2-2.1_amd64.deb &&\
 sudo rm -f /root/libpopt0_1.16-11_amd64.deb /root/rsync_3.1.2-2.1_amd64.deb
RUN conan profile new --detect default &&\
 conan profile update env.CC=clang default &&\
 conan profile update env.CXX=clang++ default &&\
 conan profile update env.CFLAGS=-fPIC default &&\
 conan profile update env.CXXFLAGS=-fPIC default &&\
 conan profile update settings.compiler.libcxx=libc++ default &&\
 conan profile update settings.cppstd=17 default
{addon}
CMD echo > /dev/null &\
 while [ 1 = 1 ]; do\
  pid=$(($! + 2));\
  sleep 600;\
  echo $pid &\
  if [ "$pid" -eq "$!" ]; then exit 0; fi;\
 done
ENV CC=clang
ENV CXX=clang++
'''

_docker_todo = '''Failed to create build container with docker using image '{image}'
You can use some remote server by setting environment CLION_BUILD_SERVER
You can use different image by setting environment CLION_BUILD_IMAGE
Or you can create docker image by command: `{self} --make-docker-image`
'''


def make_docker_image():
    temp = tempfile.mkdtemp()
    print("Downloading rsync package")
    urllib.request.urlretrieve('http://http.us.debian.org/debian/pool/main/p/popt/libpopt0_1.16-11_amd64.deb',
                               os.path.join(temp, 'libpopt0_1.16-11_amd64.deb'))
    urllib.request.urlretrieve('http://http.us.debian.org/debian/pool/main/r/rsync/rsync_3.1.2-2.1_amd64.deb',
                               os.path.join(temp, 'rsync_3.1.2-2.1_amd64.deb'))
    add_content = ''
    registry = os.path.join(os.environ.get('HOME'), '.conan', 'registry.txt')
    if os.path.isfile(registry):
        content = open(registry, 'r').read()
        open(os.path.join(temp, 'registry.txt'), 'w').write(content[0:content.find('\n\n')])
        add_content = 'COPY registry.txt /home/conan/.conan/'
    open(os.path.join(temp, 'Dockerfile'), 'w').write(_docker_script.format(addon=add_content))
    docker_image = os.environ.get("CLION_BUILD_IMAGE", "clion-build")
    print("Building docker image at folder '%s'" % temp)
    if subprocess.call(['docker', 'build', '-t', docker_image + ':latest', temp]) != 0:
        exit("Failed to create docker image '%s'" % docker_image)
    print("Removing folder '%s'" % temp)
    shutil.rmtree(temp)


def get_docker_shell(environ):
    with Lock(os.path.join(os.getenv('HOME'), '.clion.docker.create')):
        docker_image = environ.get("CLION_BUILD_IMAGE", "clion-build")
        container_lookup_args = ['docker', 'ps', '--filter', 'ancestor=' + docker_image, '-q']
        docker_id = subprocess.run(container_lookup_args, stdout=subprocess.PIPE).stdout.decode().strip()
        if len(docker_id) == 0:
            stopped_docker_id = subprocess.run(container_lookup_args + ['-a'], stdout=subprocess.PIPE).stdout.decode().strip()
            if len(stopped_docker_id) == 0:
                sys.stderr.write('Create docker from image "%s": %s\n' % (docker_image, docker_id))
                docker_id = subprocess.run(['docker', 'create', docker_image],
                                           stdout=subprocess.PIPE).stdout.decode().strip()
                if len(docker_id) == 0:
                    sys.stderr.write(_docker_todo.format(self=sys.argv[0], image=docker_image))
                    exit(1)
            else:
                docker_id = stopped_docker_id

            subprocess.call(['docker', 'start', docker_id], stdout=subprocess.DEVNULL)
        else:
            sys.stderr.write("Build on docker: %s\n" % docker_id)
        return Docker(docker_id)


class Lock:
    def __init__(self, path):
        self._path = path
    def __enter__(self):
        self._fd = open(self._path, 'w')
        fcntl.lockf(self._fd.fileno(), fcntl.LOCK_EX)
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._fd.close()
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass


class Docker(Remote):
    def __init__(self, id):
        super().__init__(host=id, params='docker exec -i ')

    def sync_artifacts(self, path):
        sys.stderr.write("Get artifacts: %s\n" % path)
        src = self._host + ':' + path
        if src[-1] == '/':
            src = src + '.'
        subprocess.call(['docker', 'cp', src, path])

    def call(self, args, desc=None, **kwargs):
        self.desc(args, desc)
        p = subprocess.Popen(['docker', 'exec', '-i', self._host, '/bin/sh'], stdin=subprocess.PIPE, **kwargs)
        p.communicate(' '.join(self._prepare_args(args)).encode())
        return p.returncode

    def run(self, args):
        sys.stderr.write("Execute: %s\n" % ' '.join(self._prepare_args(args)))
        return subprocess.run(['docker', 'exec', '-i', self._host, '/bin/sh'], input=' '.join(self._prepare_args(args)).encode(),
                              stderr=sys.stderr, stdout=subprocess.PIPE).stdout.decode().strip()

    def mkdir(self, dir):
        if self.call(['mkdir', '-p', dir, '2>/dev/null', '||',
                      'sudo', 'mkdir', '-p', dir, '&&',
                      'sudo', 'chown', '-R', '`id -u`', dir],
                     desc="Make folder: %s" % dir) != 0:
            exit("Failed to connect to build server")