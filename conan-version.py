#!/Library/Frameworks/Python.framework/Versions/3.6/bin/python3
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed


class ConanPackge:
    def __init__(self, fullname):
        sep = fullname.find('/')
        self.fullname = fullname
        self.name = fullname[0:sep]
        sep2 = fullname.find('@')
        self.version = fullname[sep + 1:sep2]
        self.author = fullname[sep2 + 1:]


def get_build_order(package, original_version):
    print('Original base package version = {data}'.format(data=original_version))
    result = 'output.json'
    if subprocess.call(['conan', 'info', '--build-order', original_version, '--json', result, package]) != 0:
        print('Failed to get --build-order for {package}'.format(package=package))
        return
    fd = open(result, 'r')
    packages = json.load(fd)
    if "groups" not in packages:
        print('Bad response from conan. There is no groups in --build-order result')
        return

    return packages["groups"][1:]

def get_package_urls(package):
    conan = subprocess.Popen(['conan', 'info', '-n', 'url', package], stdout=subprocess.PIPE)
    header = conan.stdout.readline().decode().strip()
    if header != 'PROJECT':
        exit("Unexpected output for conan info: %s" % header)
    result = {}
    for name in conan.stdout:
        url = conan.stdout.readline().decode().strip()
        result[name.decode().strip()] = url.split('URL: ', 2)[1]
    return result


def version_up(package, url, up_map, remote):
    folder = os.path.join(workdir, package.name)
    home = os.getenv('CONAN_USER_HOME', os.getenv('HOME', None))
    if home is None:
        exit('Failed to get CONAN home')
    conandir = os.path.join(home, '.conan/data', package.fullname.replace('@', '/'))
    source = os.path.join(conandir, 'source')
    if not os.path.isdir(source):
        if subprocess.call(['conan', 'install', '--build', package.name, package.fullname], stdout=subprocess.DEVNULL) != 0:
            exit('Failed to get sources for "%s"' % package.fullname)
    shutil.copytree(source, folder)
    shutil.copy(os.path.join(conandir, 'export/conanfile.py'), os.path.join(folder, 'conanfile.py'))

    # subprocess.call(['git', 'clone', url, folder], stderr=subprocess.DEVNULL)
    version = conanfile_version_up(folder, up_map)
    new_package = package.fullname, package.name + '/' + version + '@' + package.author
    if subprocess.call(['conan', 'create', folder, package.author], stdout=subprocess.DEVNULL) == 0:
        if remote is None:
            print("conan upload --remote ${CONAN_REMOTE} --all --confirm %s" % new_package)
        else:
            subprocess.call(['conan', 'upload', '--remote', remote, '--all', '--confirm', package.fullname], stdout=subprocess.DEVNULL)
    else:
        exit('Failed to create "%s"' % package.name)
    return new_package


def conanfile_version_up(folder, up_map):
    conanfile = os.path.join(folder, 'conanfile.py')
    with open(conanfile, 'r') as content_file:
        content = content_file.read()
    version = re.search(r'''^\s+version\s*=\s*['"]([^'"]*).*$''', content, re.MULTILINE)
    values = version.group(1).split('.')
    if len(values) < 3:
        values.append(1)
    else:
        values[len(values) - 1] = str(int(values[len(values) - 1]) + 1)
    content = content[0:version.start(1)] + '.'.join(values) + content[version.end(1):]
    for old, val in up_map.items():
        content = content.replace('"' + old + '"', '"' + val + '"')
        content = content.replace("'" + old + "'", "'" + val + "'")
    with open(conanfile, 'w') as content_file:
        content_file.write(content)
    return '.'.join(values)


parser = argparse.ArgumentParser(description='Conan package version updater')
parser.add_argument('--base', help='base package where version was updated', required=True)
parser.add_argument('--remote', help='conan remote to upload new packages', default=None)
parser.add_argument('package', help='package to update. all depended packages will be updated too', nargs='+')
args = parser.parse_args()

base = ConanPackge(args.base)
with tempfile.TemporaryDirectory() as workdir:
    print("Working directory: %s" % workdir)
    for package in args.package:
        package_urls = get_package_urls(package)
        orig = next((name for name in package_urls.keys() if name.startswith(base.name + '/')), None)
        if orig is None:
            print('Package "{package}" does not depended from "{base}"'.format(package=package, base=base.fullname))
            continue
        if orig == base.fullname:
            print('package "{package}" already depended from "{base}"'.format(package=package, base=base.fullname))
            continue
        build_order = get_build_order(package, orig)

        up_map = {orig: base.fullname}
        if args.remote is not None:
            print('Update from "%s" to "%s"' % (orig, base.fullname))
        for group in build_order:
            with ThreadPoolExecutor(max_workers=4) as executor:
                pkgs = (executor.submit(version_up, ConanPackge(package), package_urls[package], up_map, args.remote) for package in group)
                add = {}
                for future in as_completed(pkgs):
                    orig, updated = future.result()
                    if orig is not None:
                        add[orig] = updated
                        if args.remote is not None:
                            print('Update from "%s" to "%s"' % (orig, updated))
                up_map.update(add)

        conanfile = os.path.join(package, 'conanfile.txt')
        if os.path.isfile(conanfile):
            with open(conanfile, 'r') as content_file:
                content = content_file.read()
            for old, val in up_map.items():
                content = content.replace('\n' + old, '\n' + val)
            with open(conanfile, 'w') as content_file:
                content_file.write(content)
        elif os.path.isfile(os.path.join(package, 'conanfile.py')):
            conanfile_version_up(package, up_map)
        else:
            version_up(ConanPackge(package), package_urls[package], up_map)

exit(0)
