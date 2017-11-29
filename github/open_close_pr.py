#!/usr/bin/python3

import github3
import argparse
import logging
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..'))
from tasks import common
from prci_github.adapter import GitHubAdapter


DEFAULT_BRANCH_NAME = 'nt'
DEFAULT_FILE_NAME = 'nightly_test.txt'
DEFAULT_PR_TITLE = '[Nightly Test PR]'
NEW_BRANCH_REF = 'refs/heads/'
DEFAULT_COMMIT_MSG = 'File for Nightly Tests'
FREEIPA_PRCI_CONFIG_NAME = '.freeipa-pr-ci.yaml'

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
consoleHandler = logging.StreamHandler()
consoleHandler.setLevel(logging.DEBUG)
logger.addHandler(consoleHandler)


class NightlyTests(object):

    def __init__(self, github_token, repo):
        github = github3.login(token=github_token)
        github.session.mount('https://api.github.com',
                             GitHubAdapter())

        self.__repo = github.repository(repo['owner'],
                                        repo['name'])

    def __create_branch(self):
        last_commit = next(self.__repo.commits())
        branch_name = '{}_{}'.format(DEFAULT_BRANCH_NAME,
                                     datetime.now().strftime("%Y%m%d_%H%M%S"))

        new_branch_ref = '{}{}'.format(NEW_BRANCH_REF, branch_name)
        self.__repo.create_ref(new_branch_ref, last_commit.sha)

        return branch_name

    def __update_prci_config(self, new_prci_config, branch_name):
        """
        Updates the .freeipa-pr-ci.yaml file with the content
        of the file provided.
        """

        prci_config_file = self.__repo.file_contents(FREEIPA_PRCI_CONFIG_NAME)

        content = ''
        with open(new_prci_config, 'r') as f:
            content = ' '.join(f.readlines())

        prci_config_file.update(DEFAULT_COMMIT_MSG, content.encode(),
                                branch_name)

    def open_pr(self, args):
        # a branch is created and a commit is done in order to use them
        # to create a new PR. The content of the commit doesn't matter
        branch_name = self.__create_branch()

        # if a path to a new config file is provided, then it changes the
        # content and commit it. otherwise, it will create an empty file
        if args.prci_config:
            self.__update_prci_config(args.prci_config, branch_name)
        else:
            self.__repo.create_file(DEFAULT_FILE_NAME, DEFAULT_COMMIT_MSG,
                                    branch_name.encode(), branch_name)

        pr_title = '{} {}'.format(DEFAULT_PR_TITLE, branch_name)

        logger.info("A new PR against %s/%s will be created with "
                    "the title %s", self.__repo.owner.login,
                    self.__repo.source.name, pr_title)

        head = '{}:{}'.format(self.__repo.owner.login, branch_name)
        pr = self.__repo.create_pull(pr_title, 'master', head)
        logger.info("PR %s created", pr.number)

    def close_pr(self, args):
        pr = self.__repo.pull_request(args.pr_number)

        if not pr:
            raise argparse.ArgumentTypeError("A Pull Request with this "
                                             "number doesn't exists")

        pr.close()

        if args.close_comment:
            issue = self.__repo.issue(pr.number)
            issue.create_comment(args.close_comment)

        logger.info("PR %s closed", pr.number)

    def run(self, args):
        fnc = getattr(self, args.command)
        logger.debug('Executing %s command', args.command)
        return fnc(args)


def create_parser():
    parser = argparse.ArgumentParser(description='')
    commands = parser.add_subparsers(dest='command')

    commands.add_parser('open_pr', description="Opens a PR for Nightly Tests")

    close_pr = commands.add_parser('close_pr', description="Closes a PR")
    close_pr.add_argument('pr_number', type=int)
    close_pr.add_argument('--close_comment', type=str)

    parser.add_argument(
        '--config', type=config_file, required=True,
        help='YAML file with complete configuration.',
    )

    parser.add_argument(
        '--prci_config', type=str, required=False,
        help='An easy way to override the .freeipa-pr-ci.yml file'
    )

    return parser


def config_file(path):
    config = common.load_yaml(path)

    fields_required = ['repository', 'credentials']
    for field in fields_required:
        if field not in config:
            raise argparse.ArgumentTypeError(
                'Missing required section {} in config file', field)
    return config


def main():
    parser = create_parser()
    args = parser.parse_args()

    config = args.config
    creds = config['credentials']
    repository = config['repository']

    logger.debug('Running Open and Close PR Tool against %s/%s repo',
                 repository['owner'], repository['name'])
    nt = NightlyTests(creds['token'], repository)
    nt.run(args)


if __name__ == '__main__':
    main()

