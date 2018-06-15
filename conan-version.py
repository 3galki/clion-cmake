#!/Library/Frameworks/Python.framework/Versions/3.6/bin/python3
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib3
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


class Worker:
    def __init__(self, base: ConanPackge):
        self._base = base

    def run(self, package, url, up_map, remote):
        return self.version_up(package, url, up_map, remote)

    def set_package(self, package):
        package_urls = get_package_urls(package)
        orig = next((name for name in package_urls.keys() if name.startswith(base.name + '/')), None)
        if orig is None:
            print('Package "{package}" does not depended from "{base}"'.format(package=package, base=base.fullname))
            return False
        if orig == base.fullname:
            print('package "{package}" already depended from "{base}"'.format(package=package, base=base.fullname))
            return False
        build_order = get_build_order(package, orig)

        up_map = {orig: base.fullname}
        return True

    def get_suffix(self, folder):
        suffix = ord('a')
        res = subprocess.Popen(['conan', 'info', '-n', 'None', folder], stdout=subprocess.PIPE)
        for line in res.stdout:
            if line.decode().strip() == self._base.fullname:
                break
            suffix += 1
        return chr(suffix)

    def conanfile_version_up(self, folder, up_map):
        conanfile = os.path.join(folder, 'conanfile.py')
        with open(conanfile, 'r') as content_file:
            content = content_file.read()
        version = re.search(r'''^\s+version\s*=\s*['"]([^'"]*).*$''', content, re.MULTILINE)

        suffix = self.get_suffix(folder)

        values = version.group(1).split('.')
        if values[-1].endswith(suffix):
            values[-1] = str(int(values[0:len(values[-1]) - 1]) + 1) + suffix
        else:
            values.append('1' + suffix)

        content = content[0:version.start(1)] + '.'.join(values) + content[version.end(1):]
        for old, val in up_map.items():
            content = content.replace('"' + old + '"', '"' + val + '"')
            content = content.replace("'" + old + "'", "'" + val + "'")
        with open(conanfile, 'w') as content_file:
            content_file.write(content)
        return '.'.join(values)


class ConanWorker(Worker):
    def __init__(self, base):
        super().__init__(base)

    def get_source(self, package):
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
        return folder

    def version_up(self, package, url, up_map, remote):
        folder = self.get_source(package)

        # subprocess.call(['git', 'clone', url, folder], stderr=subprocess.DEVNULL)
        version = self.conanfile_version_up(folder, up_map)
        new_package = package.name + '/' + version + '@' + package.author
        if subprocess.call(['conan', 'create', folder, package.author], stdout=subprocess.DEVNULL) == 0:
            if remote is None:
                print("conan upload --remote ${CONAN_REMOTE} --all --confirm %s" % new_package)
            else:
                subprocess.call(['conan', 'upload', '--remote', remote, '--all', '--confirm', package.fullname], stdout=subprocess.DEVNULL)
        else:
            exit('Failed to create "%s"' % package.name)
        return new_package


class GitLabWorker(Worker):
    def __init__(self, base, url, token):
        super().__init__(base)
        self._url = url
        self._token = token
        urllib3.disable_warnings()

    def get_source(self, package, url):
        folder = os.path.join(workdir, package.name)
        # if url.startswith('git@'):
        #     url = url[4:].replace(':', '/')
        # elif url.startswith('https://'):
        #     url = url[8:]
        # else:
        #     exit("Bad repo URL: %s" % url)
        # url = 'https://gitlab-ci-token:{token}@{url}'.format(token=self._token, url=url)
        print('URL: %s' % url)
        subprocess.call(['git', 'clone', url, folder])
        return folder

    def fix_url(self, url):
        if url.startswith('git@'):
            url = url[4:].replace(':', '/')
        elif url.startswith('https://'):
            url = url[8:]
        else:
            exit("Bad repo URL: %s" % url)
        return 'https://gitlab-ci-token:{token}@{url}'.format(token=self._token, url=url)

    def version_up(self, package, url, up_map, remote):
        url = self.fix_url(url)
        folder = self.get_source(package, url)
        version = self.conanfile_version_up(folder, up_map)
        subprocess.call(['git', 'checkout', '-b', 'version/' + version], cwd=folder)
        subprocess.call(['git', 'commit', '-a', '-m', 'Upgrade versnios for conan packages'], cwd=folder)
        subprocess.call(['git', 'push', 'origin', 'version/' + version], cwd=folder)

        project_id = url.split('/', 3)[3].split('.')[0]
        print('URL: ' + url + " project: " + project_id)
        http = urllib3.PoolManager()
        r = http.request(
            method='POST',
            url=self._url + '/api/v4/projects/{id}/merge_requests'.format(id=project_id.replace('/', '%2F')),
            headers={'Private-Token': self._token},
            fields={
                'source_branch': 'version/' + version,
                'target_branch': 'master',
                'title': 'Version UP to ' + version,
                'remove_source_branch': 'true',
                'merge_when_pipeline_succeeds': 'true'
            },

        )
        result = json.loads(r.data.decode())
        status = ''
        while status != 'success':
            r1 = http.request(
                method='GET',
                url=self._url + '/api/v4/projects/{id}/merge_requests/{merge_iid}/pipelines'.format(id=result['project_id'], merge_iid=result["iid"])
            )
            tmp = json.loads(r1.data.decode())
            status = tmp[0]["status"]
            if status == "failed":
                exit("Failed to build")
            time.sleep(5)

        http.request(
            method='PUT',
            url=self._url + '/api/v4/projects/{id}/merge_requests/{merge_iid}/merge'.format(id=result['project_id'], merge_iid=result["iid"]),
            headers={'Private-Token': self._token},
        )


        pipeline_id = result["pipeline"]["id"]
        status = ''
        while status != 'success':
            r1 = http.request(
                method='GET',
                url=self._url + '/api/v4/projects/{id}/pipelines/{pipeline_id}'.format(id=result['project_id'], pipeline_id=pipeline_id)
            )
            tmp = json.loads(r1.data.decode())
            status = next((pipeline["status"] for pipeline in tmp if pipeline["id"] == pipeline_id))
            if status == "failed":
                exit("Failed to build")
            time.sleep(5)
        return package.name + '/' + version + '@' + package.author


parser = argparse.ArgumentParser(description='Conan package version updater')
parser.add_argument('--base', help='base package where version was updated', required=True)
parser.add_argument('--remote', help='conan remote to upload new packages', default=None)
parser.add_argument('--gitlab', help='GitLab URL', default=None)
parser.add_argument('--gitlab-token', help='GitLab private token')
parser.add_argument('package', help='package to update. all depended packages will be updated too', nargs='+')
args = parser.parse_args()

base = ConanPackge(args.base)
if args.gitlab is None:
    worker = ConanWorker(base)
else:
    worker = GitLabWorker(base, args.gitlab, args.gitlab_token)

with tempfile.TemporaryDirectory() as workdir:
    print("Working directory: %s" % workdir)
    for package in args.package:
        worker.set_package(package)
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
                pkgs = (executor.submit(Worker.run, worker, ConanPackge(package), package_urls[package], up_map, args.remote) for package in group)
                add = {}
                for future in as_completed(pkgs):
                    version = future.result()
                    if version is not None:
                        add[package] = version
                        if args.remote is not None:
                            print('Update from "%s" to "%s"' % (orig, version))
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
            worker.conanfile_version_up(package, up_map)
        else:
            worker.version_up(ConanPackge(package), package_urls[package], up_map)

exit(0)
