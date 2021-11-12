#!/usr/bin/env python3
import argparse
import configparser
import datetime
import email.utils
import git
import logging
import os
import pathlib
import smtplib
import shutil
from abc import ABCMeta, abstractmethod
from enum import Enum
from email.mime.multipart import MIMEMultipart
from github import Github
from email.mime.text import MIMEText

logger = None
CONFIG = None
BASE_DIR = None

GITHUB_REPO_LIST = [
    "bluez/bluez",
    "bluez/bluetooth-next",
    "blueztestbot/bluez",
    "blueztestbot/bluetooth-next"
]

REPO_SYNC_MAP = [
    {
        "name" : "bluez",
        "src_repo" : "https://git.kernel.org/pub/scm/bluetooth/bluez.git",
        "src_branch" : "master",
        "dest_repo" : "https://github.com/bluez/bluez",
        "dest_branch" : "master"
    },
    {
        "name" : "bluetooth-next",
        "src_repo" : "https://git.kernel.org/pub/scm/linux/kernel/git/bluetooth/bluetooth-next.git",
        "src_branch" : "master",
        "dest_repo" : "https://github.com/bluez/bluetooth-next",
        "dest_branch" : "master"
    },
    {
        "name" : "bluetooth-next:for-upstream",
        "src_repo" : "https://git.kernel.org/pub/scm/linux/kernel/git/bluetooth/bluetooth-next.git",
        "src_branch" : "for-upstream",
        "dest_repo" : "https://github.com/bluez/bluetooth-next",
        "dest_branch" : "for-upstream"
    },
    {
        "name" : "BluezTestBot:bluez",
        "src_repo" : "https://github.com/bluez/bluez",
        "src_branch" : "master",
        "dest_repo" : "https://github.com/BluezTestBot/bluez",
        "dest_branch" : "master"
    },
    {
        "name" : "BluezTestBot:bluetooth-next",
        "src_repo" : "https://github.com/bluez/bluetooth-next",
        "src_branch" : "master",
        "dest_repo" : "https://github.com/BluezTestBot/bluetooth-next",
        "dest_branch" : "master"
    },
    {
        "name" : "BluezTestBot:bluetooth-next:for-upstream",
        "src_repo" : "https://github.com/bluez/bluetooth-next",
        "src_branch" : "for-upstream",
        "dest_repo" : "https://github.com/BluezTestBot/bluetooth-next",
        "dest_branch" : "for-upstream"
    },
]

HEADER = '''
Hi team,

This email contains the status of the BlueZ and CI repositories in Github to
provide the synchronization status with the upstream repo and issue/PR counts.

'''

FOOTER = '''

PS: This is automated email and please do not reply to this email!

---
Regards,
Linux Bluetooth
'''

def send_email(sender, receiver, msg):
    """ Send email """

    email_cfg = CONFIG['email']
    if 'EMAIL_TOKEN' not in os.environ:
        logger.warning("missing EMAIL_TOKEN. Skip sending email")
        return

    try:
        session = smtplib.SMTP(email_cfg['server'], int(email_cfg['port']))
        session.ehlo()
        if 'starttls' not in email_cfg or email_cfg['starttls'] == 'yes':
            session.starttls()
        session.ehlo()
        session.login(sender, os.environ['EMAIL_TOKEN'])
        session.sendmail(sender, receiver, msg.as_string())
        logger.info("Successfully sent email")
    except Exception as e:
        logger.error("Exception: {}".format(e))
    finally:
        session.quit()
    logger.info("Sending email done")

def compose_and_send(messages):
    """ Compose the email and send to the mailing list """

    email_cfg = CONFIG['email']
    sender = email_cfg['user']

    receivers = []
    if 'only-maintainers' in email_cfg and email_cfg['only-maintainers'] == 'yes':
        # Send only to the addresses in the 'maintainers'
        maintainers = "".join(email_cfg['maintainers'].splitlines()).split(",")
        receivers.extend(maintainers)
    else:
        # Send to default-to address and submitter
        receivers.append(email_cfg['default-to'])

    today = datetime.datetime.now().strftime("%Y-%m-%d")

    # Create message
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = ", ".join(receivers)
    msg['Subject'] = "[Intel - internal] BlueZ Repository Status - %s" % today

    body = HEADER + '\n'
    body += messages + '\n'
    body += FOOTER

    msg.attach(MIMEText(body, 'plain'))
    logger.debug("Mail Message: \n{}".format(msg))

    # Send email
    send_email(sender, receivers, msg)

def git_clone_repo(repo, to_path, branch='master', depth=1, delete_exist=True):
    """ Clone repo with gitpython API and return repo object """

    # Check input parameter
    if os.path.exists(to_path):
        if delete_exist:
            shutil.rmtree(to_path)
            logger.info("Repo to_path is already exist and removed")
        else:
            logger.error("Repo to_path is already exist")
            return None

    return git.Repo.clone_from(repo, to_path, branch=branch, depth=depth)

def github_init(repo):
    """ Initialize github object """

    return Github(os.environ['GITHUB_TOKEN']).get_repo(repo)

def github_get_issues_only(repo, state='open'):
    """ Get a list of issues from the repo """

    issues_only = []
    all_issues = repo.get_issues(state=state)
    for issue in all_issues:
        if not issue.pull_request:
            issues_only.append(issue)

    return issues_only

class Verdict(Enum):
    PENDING = 0
    PASS = 1
    FAIL = 2
    ERROR = 3
    SKIP = 4
    WARNING = 5

class StatusBase(metaclass=ABCMeta):
    """ Base class for Status task """

    result = None
    verdict = None

    def add_result(self, result):
        if not self.result:
            self.result = result
        else:
            self.result += '\n' + result

    def get_result(self):
        return self.result

    @abstractmethod
    def check(self):
        raise NotImplementedError


class RepoSyncStatus(StatusBase):
    """
    Repo Sync status: Clone two repos (src_repo and dest_repo) and compare
    the top commits and update the message with the result.
    """

    def __init__(self, name, src_repo, src_branch, dest_repo, dest_branch):
        self.name = name
        self.src_repo = src_repo
        self.dest_repo = dest_repo
        self.src_branch = src_branch
        self.dest_branch = dest_branch
        self.base_dir = os.path.join(BASE_DIR, name)

        # Add information to the result string
        self.add_result("Repo Sync: %s" % self.name)

        # Just to print out the result
        self.verdict = Verdict.PASS

    def check(self):
        logger.debug("Check Repo Sync Status")

        # Clone src repo + branch
        logger.debug("1. Clone src_repo: {}({})".format(self.src_repo,
                                                        self.src_branch))
        repo_src = git_clone_repo(self.src_repo, self.base_dir + "_src", self.src_branch)
        if not repo_src:
            logger.error("Unable to clone repo: %s" % self.src_repo)
            self.add_result("   Results: Failed (Clone src repo failed)")
            self.verdict = Verdict.ERROR
            return -1

        # Clone dest repo + branch
        logger.debug("2. Clone dest_repo: {}({})".format(self.dest_repo,
                                                         self.dest_branch))
        repo_dest = git_clone_repo(self.dest_repo, self.base_dir + "_dest",
                                   self.dest_branch)
        if not repo_dest:
            logger.error("Unable to clone repo: %s" % self.dest_repo)
            self.add_result("   Results: Fail (Clone dest repo failed)")
            self.verdict = Verdict.ERROR
            return -1

        # Compare HEAD
        logger.debug("3. Compare HEADs of both repos")
        src_head_sha = repo_src.head.commit.hexsha
        dest_head_sha = repo_dest.head.commit.hexsha

        logger.debug("src head:  %s" % src_head_sha)
        logger.debug("dest head: %s" % dest_head_sha)
        self.add_result("   SRC HEAD:  %s" % src_head_sha)
        self.add_result("   DEST HEAD: %s" % dest_head_sha)
        if src_head_sha != dest_head_sha:
            logger.info("src repo and dest repo are not synced")
            self.add_result("   Result: Fail (SHA mismatch)")
            self.verdict = Verdict.FAIL
            return -1

        logger.info("src repo and dest repo are synced")
        self.add_result("   Result: Pass")
        return 0


class GithubRepoStatus(StatusBase):
    """
    Github Repo status
    """

    def __init__(self, repo):
        self.repo = repo

        # Add information to the result string
        self.add_result("Github Repo: %s" % self.repo)
        self.verdict = Verdict.WARNING

    def check(self):
        logger.debug("Check Repo Status and Information")

        logger.debug("1. Initialize the github repo(%s)" % self.repo)
        github_repo = github_init(self.repo)
        if not github_repo:
            logger.error("Failed to initialized the repo: %s" % self.repo)
            self.add_result("   Result: Fail (Failed to init github repo)")
            self.verdict = Verdict.ERROR
            return -1

        # Get the number of PR
        logger.debug("2. Get the number of open Pull Requests")
        repo_prs = github_repo.get_pulls(state='open')
        logger.debug("   PRs:    %d" % repo_prs.totalCount)
        self.add_result("   PRs:    %d" % repo_prs.totalCount)

        # Get the number of Issues
        logger.debug("3. Get the number of open issues")
        repo_issues = github_get_issues_only(github_repo)
        logger.debug("   Issues: %d" % len(repo_issues))
        self.add_result("   Issues: %d" % len(repo_issues))

        return 0

def compare_repo_branch(src_repo, dest_repo, src_branch='master', dest_branch='master'):

    logger.debug("Create RepoSyncStatus objec")
    repo_sync = RepoSyncStatus("test", src_repo, dest_repo, src_branch, dest_branch)

    logger.debug("calling check")
    repo_sync.check()

    print("obj message: " + repo_sync.result)


def check_repo_sync(sync_list):
    """ Run repo sync status """

    for index, item in enumerate(REPO_SYNC_MAP):
        logger.debug("### Repo Sync Map#%d ###" % (index + 1))
        logger.debug("   name:        " + item['name'])
        logger.debug("   src_repo:    " + item['src_repo'])
        logger.debug("   src_branch:  " + item['src_branch'])
        logger.debug("   dest_repo:   " + item['dest_repo'])
        logger.debug("   dest_branch: " + item['dest_branch'])

        repo_sync = RepoSyncStatus(item['name'],
                                   item['src_repo'],
                                   item['src_branch'],
                                   item['dest_repo'],
                                   item['dest_branch'])
        repo_sync.check()
        sync_list.append(repo_sync)

def check_repo_status(check_list):
    """ Run repo PR status """

    for index, repo_name in enumerate(GITHUB_REPO_LIST):
        logger.debug("### Github Repo Status#%d ###" % (index + 1))
        logger.debug("   repo: %s" % repo_name)

        # Create Github Repo Status object
        check_repo = GithubRepoStatus(repo_name)
        check_repo.check()
        check_list.append(check_repo)

def collect_results(task_list):
    """ Collect the list from the check task and return the string """
    results = ""

    for task in task_list:
        if task.verdict != Verdict.PASS:
            results = results + '\n' + task.get_result()
    return results

def ci_status(args):
    """ Run CI Status """

    sync_list = []
    check_list = []
    messages = ""

    # Check repo sync
    check_repo_sync(sync_list)
    # Check repo PR status
    check_repo_status(check_list)

    # Collect all results
    messages += "##### Repository Synchronization Status #####\n"
    messages += collect_results(sync_list)
    messages += "\n\n"
    messages += "##### Github Repository Status/Information #####\n"
    messages += collect_results(check_list)

    logger.debug("Email Messages: \n**********\n%s\n**********\n" % messages)

    # Compose email and send
    compose_and_send(messages)

def init_logging(verbose):
    """ Initialize logger. Default to INFO """

    global logger

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if verbose:
        logger.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s: %(levelname)-8s: %(message)s')
    ch.setFormatter(formatter)

    logger.addHandler(ch)

    logger.info("Initialized the logger: level=%s",
                 logging.getLevelName(logger.getEffectiveLevel()))

def init(args):
    """ Initialization """

    global BASE_DIR
    global CONFIG

    # Base dir
    if not os.path.exists(args.base_dir):
        os.mkdir(args.base_dir)
    BASE_DIR = args.base_dir

    # logging
    init_logging(args.verbose)

    # parse configuration
    if not os.path.exists(args.config):
        logger.error("Cannot find the configuration file: %s" % args.config)
        return -1

    CONFIG = configparser.ConfigParser()
    CONFIG.read(args.config)

    logger.info("Initialization completed")

def parse_args():

    parser = argparse.ArgumentParser(description="Check the various CI status")
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Display debugging info')
    parser.add_argument('-b', '--base-dir', required=False,
                        default=os.getcwd(), type=pathlib.Path,
                        help='Base directory of repos')
    parser.add_argument('-c', '--config', required=False,
                        default='./config.ini', type=pathlib.Path,
                        help='Path to the configuration file')

    return parser.parse_args()

def main():

    args = parse_args()
    init(args)
    ci_status(args)

if __name__ == "__main__":
    main()
