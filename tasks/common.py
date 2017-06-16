import abc
import collections
import errno
import jinja2
import logging
import os
import subprocess
import threading

import constants


LOG_FILE_HANDLER = None
LOG_FORMAT = '%(asctime)-15s %(levelname)8s  %(message)s'


class TaskException(Exception):
    def __init__(self, task, msg=None):
        self.task = task
        if msg is None:
            self.msg = 'execution failed'
        else:
            self.msg = msg

    def __str__(self):
        return '{task} {msg}'.format(
            task=self.task,
            msg=self.msg)


class TimeoutException(TaskException):
    def __init__(self, task):
        super(TimeoutException, self).__init__(task)
        self.msg = 'timed out after {timeout}s'.format(
            timeout=self.task.timeout)


class PopenException(TaskException):
    def __init__(self, task):
        super(PopenException, self).__init__(task)
        self.msg = 'exited with error code {error}'.format(
            error=self.task.returncode)


class Task(collections.Callable):
    __metaclass__ = abc.ABCMeta

    def __init__(self, timeout=120):
        self.timeout = timeout
        self.tasks = []
        self.exc = None

    def execute_subtask(self, task):
        self.tasks.append(task)
        task()

    @abc.abstractmethod
    def _run(self):
        pass

    def _terminate(self):
        pass

    def terminate(self):
        for task in self.tasks:
            task.terminate()
        self._terminate()

    def __target(self):
        self.exc = None
        try:
            self._run()
        except TaskException as exc:
            self.exc = exc

    def __call__(self):
        logging.info('Executing: {task}'.format(task=self))
        thread = threading.Thread(target=self.__target)
        thread.start()
        thread.join(self.timeout)
        if thread.is_alive():
            self.terminate()
            thread.join()
            raise TimeoutException(self)
        if self.exc is not None:
            # Re-raise exception from other thread
            raise self.exc

    def __str__(self):
        return type(self).__name__


class FallibleTask(Task):
    def __init__(self, raise_on_err=True, **kwargs):
        super(FallibleTask, self).__init__(**kwargs)
        self.raise_on_err = raise_on_err

    def __call__(self):
        try:
            super(FallibleTask, self).__call__()
        except TaskException as exc:
            if self.raise_on_err:
                raise exc
            else:
                logging.warning(exc)


class PopenTask(FallibleTask):
    def __init__(self, cmd, shell=False, env=None, **kwargs):
        super(PopenTask, self).__init__(**kwargs)
        self.cmd = cmd
        self.shell = shell
        self.env = env
        self.process = None
        self.returncode = None
        if self.env is not None:
            self.env = os.environ.copy()
            self.env.update(env)

    def _run(self):
        self.process = subprocess.Popen(
            self.cmd,
            shell=self.shell,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)

        for line in iter(self.process.stdout.readline, b''):
            logging.debug(line.rstrip('\n'))

        self.process.wait()
        self.returncode = self.process.returncode
        self.process = None
        if self.returncode != 0:
            raise PopenException(self)

    def _terminate(self):
        if self.process is None:
            return
        try:
            self.process.terminate()
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                # ESRCH -> process doesn't exist (already ended)
                raise exc

    def __str__(self):
        if not isinstance(self.cmd, basestring):
            cmd = ' '.join(self.cmd)
        else:
            cmd = self.cmd
        return 'Process "{cmd}"'.format(cmd=cmd)


def init_logging():
    global LOG_FILE_HANDLER
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler('runner.log', mode='w')
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT)
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(ch)
    LOG_FILE_HANDLER = fh


def create_file_from_template(template_path, dest, data):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(constants.TEMPLATE_DIR))
    template = env.get_template(template_path)
    rendered_template = template.render(**data)

    with open(dest, "wb") as fh:
        fh.write(rendered_template)