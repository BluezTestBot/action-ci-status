#!/usr/bin/env python3
import os
import sys
import logging
import argparse
import subprocess
import re
import smtplib
import email.utils
import requests
import configparser
from github import Github
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = None

github_repo = None
github_pr = None
patchwork_sid = None

config = None

PATCHWORK_BASE_URL = "https://patchwork.kernel.org/api/1.1"

FAIL_MSG = '''
This is automated email and please do not reply to this email!

Dear submitter,

Thank you for submitting the patches to the linux bluetooth mailing list.
While we are preparing for reviewing the patches, we found the following
issue/warning.


Test Result:
Checkbuild Failed

Patch Series:
{}

Outputs:
{}


---
Regards,
Linux Bluetooth
'''

def requests_url(url):
    """ Helper function to requests WEB API GET with URL """

    resp = requests.get(url)
    if resp.status_code != 200:
        raise requests.HTTPError("GET {}".format(resp.status_code))

    return resp

def patchwork_get_series(sid):
    """ Get series detail from patchwork """

    url = PATCHWORK_BASE_URL + "/series/" + sid
    req = requests_url(url)

    return req.json()

def get_pw_sid(pr_title):
    """
    Parse PR title prefix and get PatchWork Series ID
    PR Title Prefix = "[PW_S_ID:<series_id>] XXXXX"
    """

    try:
        sid = re.search(r'^\[PW_SID:([0-9]+)\]', pr_title).group(1)
    except AttributeError:
        logging.error("Unable to find the series_id from title %s" % pr_title)
        sid = None

    return sid

def github_post_comment(msg):
    """ Post message to PR comment """

    # TODO: If the comment alrady exist, edit instead of create new one

    github_pr.create_issue_comment(msg)

def checkbuild_success_msg(extra_msg=None):
    """ Generate success message """

    msg = "**Checkbuild: PASS**\n\n"
    if extra_msg != None:
        msg += extra_msg

    return msg

def checkbuild_fail_msg(output):
    """ Generate fail message with output """

    msg = "**Checkbuild: FAIL**\n\n"
    msg = "Output:\n"
    msg += "```\n"
    msg += output
    msg += "```\n"

    return msg

def run_cmd(*args, cwd=None):
    """ Run command and return return code, stdout and stderr """

    cmd = []
    cmd.extend(args)
    cmd_str = "{}".format(" ".join(str(w) for w in cmd))
    logging.info("CMD: %s" % cmd_str)

    stdout = ""
    try:
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                bufsize=1,
                                universal_newlines=True,
                                cwd=cwd)
    except OSError as e:
        logging.error("ERROR: failed to run cmd: %s" % e)
        return (-1, None, None)

    for line in proc.stdout:
        logging.debug(line.rstrip('\n'))
        stdout += line

    # stdout is consumed in previous line. so, communicate() returns empty
    _ignore, stderr = proc.communicate()

    logging.debug(">> STDERR")
    logging.debug("/n{}".format(stderr))

    return (proc.returncode, stdout, stderr)

def send_email(sender, receiver, msg):
    """ Send email """

    email_cfg = config['email']

    if 'EMAIL_TOKEN' not in os.environ:
        logging.warning("missing EMAIL_TOKEN. Skip sending email")
        return

    try:
        session = smtplib.SMTP(email_cfg['server'], int(email_cfg['port']))
        session.ehlo()
        if 'starttls' not in email_cfg or email_cfg['starttls'] == 'yes':
            session.starttls()
        session.ehlo()
        session.login(sender, os.environ['EMAIL_TOKEN'])
        session.sendmail(sender, receiver, msg.as_string())
        logging.info("Successfully sent email")
    except Exception as e:
        logging.error("Exception: {}".format(e))
    finally:
        session.quit()

    logging.info("Sending email done")

def notify_failure(stdout, stderr):
    """ Send failure to mailing list """

    email_cfg = config['email']

    # sender = 'bluez.test.bot@gmail.com'
    sender = email_cfg['user']

    # Get series detail from Patchwork with github PR
    series = patchwork_get_series(patchwork_sid)
    logging.debug("Got Patchwork Series: {}".format(series))

    receivers = []
    if 'only-maintainers' in email_cfg and email_cfg['only-maintainers'] == 'yes':
        # Send only to the addresses in the 'maintainers'
        maintainers = "".join(email_cfg['maintainers'].splitlines()).split(",")
        receivers.extend(maintainers)
    else:
        # Send to default-to address and submitter
        receivers.append(email_cfg['default-to'])
        receivers.append(series['submitter']['email'])

    patches = series['patches']
    patch_1 = patches[0]
    patch_list = ""
    for patch in patches:
        patch_list += patch['name'] + "\n"

    # Create message
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = ", ".join(receivers)
    msg['Subject'] = "RE: " + series['name']

    # Message Header
    msg.add_header('In-Reply-To', patch_1['msgid'])
    msg.add_header('References', patch_1['msgid'])

    body = FAIL_MSG.format(patch_list, stderr)
    logging.debug("Message Body: %s" % body)
    msg.attach(MIMEText(body, 'plain'))

    logging.debug("Mail Message: {}".format(msg))

    # Send email
    send_email(sender, receivers, msg)

def process_failure(stdout, stderr):
    """
    When the check fails, it post the message to github PR and sent email to
    the mailing list (if enabled in config)
    """

    logging.debug("Post fail message to PR")
    github_post_comment(checkbuild_fail_msg(stderr))

    # Send email to mailing list
    if 'enable' not in config['email'] or config['email']['enable'] == 'yes':
        notify_failure(stdout, stderr)

def process_success(stdout, stderr):
    """
    When the check success, it simply post the message to github PR.
    No need to send email to the mailing list
    """

    logging.debug("Post success message to PR")
    github_post_comment(checkbuild_success_msg())

def ci_status(args):
    """ Check CI status """
    
    # TBD
    pass


def init_logging(verbose):
    """ Initialize logger. Default to INFO """

    global logger

    logger = logging.getLogger('')
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s:%(levelname)-8s:%(message)s')
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    logger.setLevel(logging.INFO)
    if verbose:
        logger.setLevel(logging.DEBUG)

    logging.info("Initialized the logger: level=%s",
                 logging.getLevelName(logger.getEffectiveLevel()))

def init_config():
    """ Read config.ini """

    global config

    config = configparser.ConfigParser()
    config.read("/config.ini")

def init_github(args):
    """ Initialize github object """

    global github_repo
    global github_pr
    global patchwork_sid

    github_repo = Github(os.environ['GITHUB_TOKEN']).get_repo(args.repo)
    github_pr = github_repo.get_pull(args.pull_request)
    patchwork_sid = get_pw_sid(github_pr.title)

def parse_args():
    parser = argparse.ArgumentParser(
        description="Check build with commits in pull request")

    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Display debugging info')

    return parser.parse_args()

def main():
    args = parse_args()

    init_logging(args.verbose)

    init_config()

    init_github(args)

    ci_status(args)

if __name__ == "__main__":
    main()
