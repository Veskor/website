import email
import imaplib
import json
from email.header import decode_header
from socket import gaierror

from cryptography.fernet import Fernet
from flask import current_app
from flask_jwt_extended import get_jwt_identity
from flask_smorest import Blueprint, abort
from freenit.api.methodviews import ProtectedMethodView

from sortedcontainers import SortedDict

from ..models.mail import Mail
from ..schemas.mail import MailDirSchema, MailDirSelectSchema, MailSchema
from ..utils import sendmail

blueprint = Blueprint('mail', 'mail')
host = 'mail.tilda.center'
port = 993


def find_parent(path, folders):
    if len(path) == 2:
        return folders[path[0]]
    return find_parent(path[1:], folders)


def parse_mailbox(data, folders):
    raw_flags, b, c = data.partition(')')
    flags = raw_flags.replace('(', '').replace('\\', '').strip().split()
    separator = c[2]
    folder = c[5:].replace('"', '')
    path = folder.split(separator)
    if len(path) == 1:
        folders[path[0]] = {
            'flags': flags,
            'children': {},
        }
    else:
        parent = find_parent(path, folders)
        parent['children'][path[-1]] = {
            'flags': flags,
            'children': {},
        }


@blueprint.route('', endpoint='mail')
class MailDetailAPI(ProtectedMethodView):
    @blueprint.arguments(MailSchema)
    @blueprint.response(MailSchema)
    def post(self, args):
        """Create mail"""
        mail = Mail(fromAddr='meka@tilda.center', **args)
        root = current_app.config['PROJECT_ROOT']
        key_path = f'{root}/secret.key'
        with open(key_path, 'rb') as key_file:
            key = key_file.read()
            f = Fernet(key)
            data = json.loads(get_jwt_identity())
            password = f.decrypt(data['password'].encode()).decode('utf-8')
            sendmail(current_app.config, mail.to, mail.email, password)
        return mail


@blueprint.route('/folders/<identity>', endpoint='folder')
class MailDirAPI(ProtectedMethodView):
    @blueprint.response(MailDirSelectSchema)
    def get(self, identity):
        root = current_app.config['PROJECT_ROOT']
        key_path = f'{root}/secret.key'
        with open(key_path, 'rb') as key_file:
            key = key_file.read()
            f = Fernet(key)
            data = json.loads(get_jwt_identity())
            uid = data['uid']
            domain = data['domain']
            username = f'{uid}@{domain}'
            password = f.decrypt(data['password'].encode()).decode('utf-8')
        try:
            imap = imaplib.IMAP4_SSL(host, port)
        except gaierror:
            abort(404, message=f'Host {host} not found!')
        try:
            resp, data = imap.login(username, password)
        except imaplib.IMAP4.error:
            abort(403, message='Login failed!')
        mails = []
        if resp == 'OK':
            resp, messages = imap.select(identity)
            if resp == 'OK':
                message_number = int(messages[0])
                minimum = max(message_number - 10, 0)
                for index in range(message_number, minimum, -1):
                    resp, data = imap.fetch(str(index), '(RFC822)')
                    for response in data:
                        if isinstance(response, tuple):
                            message = email.message_from_bytes(response[1])
                            subject = decode_header(message['Subject'])[0][0]
                            fromAddr = decode_header(message['From'])[0][0]
                            rawto = message['To'] or ''
                            to = decode_header(rawto)[0][0]
                            rawType = message['Content-Type']
                            mailType = decode_header(rawType)[0][0]
                            semiPos = mailType.find(';')
                            if semiPos < 0:
                                semiPos = len(mailType)
                            if isinstance(subject, bytes):
                                subject = subject.decode()
                            mail = {
                                'subject': subject,
                                'fromAddr': fromAddr,
                                'to': to,
                                'type': mailType[0:semiPos],
                            }
                            if message.is_multipart():
                                mail['message'] = ''
                                for part in message.walk():
                                    content_type = part.get_content_type()
                                    content_disposition = str(
                                        part.get('Content-Disposition'))
                                    try:
                                        body = part.get_payload(
                                            decode=True).decode()
                                    except:
                                        pass
                                    if content_type == 'text/plain':
                                        if 'attachment' not in content_disposition:
                                            mail['message'] += body
                            else:
                                content_type = message.get_content_type()
                                payload = message.get_payload(decode=True)
                                body = payload.decode('utf-8', 'ignore')
                                mail['message'] = body
                            mails.append(mail)
        return {
            'mails': mails,
            'name': identity,
        }


@blueprint.route('folders', endpoint='folders')
class MailDirListAPI(ProtectedMethodView):
    @blueprint.response(MailDirSchema)
    def get(self):
        root = current_app.config['PROJECT_ROOT']
        key_path = f'{root}/secret.key'
        with open(key_path, 'rb') as key_file:
            key = key_file.read()
            f = Fernet(key)
            data = json.loads(get_jwt_identity())
            uid = data['uid']
            domain = data['domain']
            username = f'{uid}@{domain}'
            password = f.decrypt(data['password'].encode()).decode('utf-8')
        folders = SortedDict()
        try:
            imap = imaplib.IMAP4_SSL(host, port)
        except gaierror:
            abort(404, message=f'Host {host} not found!')
        try:
            resp, data = imap.login(username, password)
        except imaplib.IMAP4.error:
            abort(403, message='Login failed!')
        if resp == 'OK':
            resp, data = imap.list('""', '*')
            if resp == 'OK':
                for mbox in data:
                    parse_mailbox(bytes.decode(mbox), folders)
        return {'folders': folders}
