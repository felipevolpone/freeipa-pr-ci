import logging
import os

from . import constants
from .common import PopenTask, PopenException, FallibleTask, TaskException


def with_vagrant(func):
    def wrapper(self, *args, **kwargs):
        try:
            __setup_provision(self)
        except TaskException as exc:
            logging.critical('vagrant or provisioning failed')
            raise exc
        else:
            func(self, *args, **kwargs)
        finally:
            if not self.no_destroy:
                self.execute_subtask(
                    VagrantCleanup(raise_on_err=False))

    return wrapper


def with_vagrant_ad(func):
    def wrapper(self, *args, **kwargs):
        try:
            __setup_provision_ad(self)
        except TaskException as exc:
            logging.critical('vagrant or provisioning failed')
            raise exc
        else:
            func(self, *args, **kwargs)
        finally:
            if not self.no_destroy:
                self.execute_subtask(
                    VagrantCleanup(raise_on_err=False))

    return wrapper


def retry(times=1):
    def decorator(func):
        def wrapper(*args, **kwargs):
            counter = 0
            while counter < times:
                counter += 1
                try:
                    func(*args, **kwargs)
                except:
                    pass

        return wrapper
    return decorator



def __setup_provision_ad(task):
    """
    This tries to execute the provision twice due to
    problems described in issue #20
    """
    task.execute_subtask(
        VagrantBoxDownload(
            box_name=task.template_name,
            box_version=task.template_version,
            link_image=task.link_image,
            timeout=None))

    @retry(times=3)
    def setup_ad_machines():
        task.execute_subtask(VagrantUpADRoot(timeout=None))
        task.execute_subtask(VagrantUpADForest(timeout=None))

    def prepare_ansible_config():
        cfg_path = '.vagrant/provisioners/ansible/inventory/vagrant_ansible_inventory'
        task.execute_subtask(PopenTask(['mkdir', '-p', '.vagrant/provisioners/ansible/inventory/']))
        task.execute_subtask(PopenTask(['touch', cfg_path]))

    def cfg_for_windows():
        cfg_path = '.vagrant/provisioners/ansible/inventory/vagrant_ansible_inventory'
        windows_part = '[windows]\n'
        linux_part = ''
        windows_vars = """[windows:vars]
ansible_winrm_server_cert_validation=ignore
ansible_connection=winrm
        """
        with open(cfg_path, 'r') as fl:
            for line in fl:
                if line.startswith('root') or line.startswith('forest'):
                    windows_part += line + '\n'
                else:
                    linux_part += line + '\n'

        with open(cfg_path, 'w') as fl:
            fl.write(linux_part)
            fl.write(windows_part)
            fl.write(windows_vars)

    # @retry(times=2)
    def install_ad():
        cfg_path = '.vagrant/provisioners/ansible/inventory/vagrant_ansible_inventory'
        task.execute_subtask(PopenTask(['cat', cfg_path]))
        task.execute_subtask(ProvisionADVMs(timeout=None))

    try:
        prepare_ansible_config()
        setup_ad_machines()
        cfg_for_windows()
        install_ad()
        task.execute_subtask(VagrantProvision(timeout=None))
        task.execute_subtask(VagrantUpAD(timeout=None))
    except Exception as exc:
        logging.debug(exc, exc_info=True)
        logging.info("Failed to provision/up VM. Trying it again")
        task.execute_subtask(VagrantCleanup(raise_on_err=False))
        setup_ad_machines()
        task.execute_subtask(VagrantUpAD(timeout=None))
        task.execute_subtask(VagrantProvision(timeout=None))


def __setup_provision(task):
    """
    This tries to execute the provision twice due to
    problems described in issue #20
    """
    task.execute_subtask(
        VagrantBoxDownload(
            box_name=task.template_name,
            box_version=task.template_version,
            link_image=task.link_image,
            timeout=None))

    try:
        task.execute_subtask(VagrantUp(timeout=None))
        task.execute_subtask(VagrantProvision(timeout=None))
    except Exception as exc:
        logging.debug(exc, exc_info=True)
        logging.info("Failed to provision/up VM. Trying it again")
        task.execute_subtask(VagrantCleanup(raise_on_err=False))
        task.execute_subtask(VagrantUp(timeout=None))
        task.execute_subtask(VagrantProvision(timeout=None))


class VagrantTask(FallibleTask):
    def __init__(self, **kwargs):
        super(VagrantTask, self).__init__(**kwargs)
        self.timeout = kwargs.get('timeout', None)


class ProvisionADVMs(VagrantTask):
    def _run(self):
        self.execute_subtask(PopenTask(["ansible-playbook -vvv /root/freeipa-pr-ci/ansible/provision_ad.yml -i .vagrant/provisioners/ansible/inventory/vagrant_ansible_inventory"], timeout=None, raise_on_err=False))


class VagrantUp(VagrantTask):
    def _run(self):
        self.execute_subtask(PopenTask(['vagrant', 'up', '--no-provision', '--parallel'],
                             timeout=None, raise_on_err=False))

class VagrantUpAD(VagrantTask):
    def _run(self):
        self.execute_subtask(PopenTask(['vagrant', 'up', 'master', 'controller',
                                        '--no-provision', '--parallel'],
                             timeout=None, raise_on_err=False))

class VagrantUpADRoot(VagrantTask):
    def _run(self):
        self.execute_subtask(PopenTask(['vagrant', 'up', 'root'],
                             timeout=None, raise_on_err=False))


class VagrantUpADForest(VagrantTask):
    def _run(self):
        self.execute_subtask(PopenTask(['vagrant', 'up', 'forest'],
                             timeout=None, raise_on_err=False))


class VagrantProvision(VagrantTask):
    def _run(self):
        self.execute_subtask(
            PopenTask(['vagrant', 'provision'],
                      timeout=None, raise_on_err=False))


class VagrantCleanup(VagrantTask):
    def _run(self):
        try:
            self.execute_subtask(
                PopenTask(['vagrant', 'destroy']))
        except PopenException:
            self.execute_subtask(
                PopenTask(['pkill', '-9', 'bin/vagrant'],
                          raise_on_err=False))
            self.execute_subtask(
                PopenTask(['systemctl', 'restart', 'libvirt'],
                          raise_on_err=False))
            self.execute_subtask(
                PopenTask(['vagrant', 'destroy'],
                          raise_on_err=False))


class VagrantBoxDownload(VagrantTask):
    def __init__(self, box_name, box_version, link_image=True, **kwargs):
        """
        link_image: if True, a symbolic link will be created in libvirt to
                    conserve storage (otherwise, libvirt copies it by default)
        """
        super(VagrantBoxDownload, self).__init__(**kwargs)
        self.box = VagrantBox(box_name, box_version)
        self.link_image = True

    def _run(self):
        if not self.box.exists():
            try:
                self.execute_subtask(
                    PopenTask([
                        'vagrant', 'box', 'add', self.box.name,
                        '--box-version', self.box.version,
                        '--provider', self.box.provider],
                        timeout=None))
            except TaskException as exc:
                logging.error('Box download failed')
                raise exc

        # link box to libvirt
        if self.link_image and not self.box.libvirt_exists():
            try:
                self.execute_subtask(
                    PopenTask([
                        'ln', self.box.vagrant_path, self.box.libvirt_path]))
                self.execute_subtask(
                    PopenTask([
                        'chown', 'qemu:qemu', self.box.libvirt_path]))
                self.execute_subtask(
                    PopenTask(['virsh', 'pool-refresh', 'default']))
            except TaskException as exc:
                logging.warning('Failed to create libvirt link to image')
                raise exc


class VagrantBox(object):
    def __init__(self, name, version, provider="libvirt"):
        self.name = name
        self.version = version
        self.provider = provider

    @property
    def escaped_name(self):
        return self.name.replace(
            '/', '-VAGRANTSLASH-')

    @property
    def vagrant_path(self):
        return constants.VAGRANT_IMAGE_PATH.format(
            name=self.escaped_name,
            version=self.version,
            provider=self.provider)

    @property
    def libvirt_name(self):
        return '{escaped_name}_vagrant_box_image'.format(
            escaped_name=self.escaped_name)

    @property
    def libvirt_path(self):
        return constants.LIBVIRT_IMAGE_PATH.format(
            libvirt_name=self.libvirt_name,
            version=self.version)

    def exists(self):
        return os.path.exists(self.vagrant_path)

    def libvirt_exists(self):
        return os.path.exists(self.libvirt_path)
