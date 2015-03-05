#!/usr/bin/env python
import argparse
import inspect
import importlib
import logging
import os
import pickle
import pkgutil
import random
import requests
import jabberbot.commands
from threading import Timer

import feedparser
from microsofttranslator import Translator
from sleekxmpp import ClientXMPP


logger = logging.getLogger(__name__)


class MUCBot(ClientXMPP):
    _NO_VOTINGS_MESSAGE = 'No votings at the moment'
    _CMD_PREFIX = '!'

    def __init__(self, jid, password, muc_room, muc_nick,
                 trans_client_id, trans_client_sec):
        super().__init__(jid, password)
        self.commands = {}
        self._muc_room = muc_room
        self._muc_nick = muc_nick
        cmdpath = jabberbot.commands.__path__
        for module_finder, name, ispkg in pkgutil.iter_modules(cmdpath):
            module = importlib.import_module('jabberbot.commands.' + name)
            if not hasattr(module, 'run_command'):
                continue
            module_name = module.__name__  # jabberbot.commands.foo
            command_name = module_name.rsplit('.', 1)[1]  # foo
            self.commands[command_name] = module
        self.register_plugin('xep_0045')
        self.add_event_handler('session_start', self.start)
        self.add_event_handler('session_end', self.end)
        self.add_event_handler('message', self.message)
        self.add_event_handler('muc::{}::got_online'.format(muc_room),
                               self.muc_got_online)
        self._nicks_filename = 'subject_nicks'
        dirpath = os.path.dirname(os.path.realpath(__file__))
        filepath = os.path.join(dirpath, self._nicks_filename)
        if not os.path.exists(filepath):
            with open(filepath, 'w+b') as f:
                pickle.dump(set(), f)
        self._timer = Timer(random.randint(3600, 43200), self._change_subject)
        self._timer.start()
        # self._vote_subject = None
        # self._votes_up = set()
        # self._votes_down = set()
        # self._trans_client_id = trans_client_id
        # self._trans_client_sec = trans_client_sec
        # self._nicks_filename = 'subject_nicks'
        # dirpath = os.path.dirname(os.path.realpath(__file__))
        # filepath = os.path.join(dirpath, self._nicks_filename)
        # if not os.path.exists(filepath):
        #     with open(filepath, 'w+b') as f:
        #         pickle.dump(set(), f)
        # self._timer = Timer(random.randint(3600, 43200),
        #                     self._change_subject)
        # self._timer.start()
        # self._cmds = {'help': self._help,
        #               'wiki': self._wikipedia
        # self._muc_cmds = {'help': self._help,
        #                   'vstart': self._vote_start,
        #                   'vup': self._vote_up,
        #                   'vdown': self._vote_down,
        #                   'vstat': self._vote_stat,
        #                   'vend': self._vote_end,
        #                   'slap': self._slap,
        #                   'meal': self._meal,
        #                   'hug': self._hug,
        #                   'kiss': self._kiss,
        #                   'wiki': self._wikipedia,
        #                   'bday': self._birthday

    def start(self, event):
        self.send_presence()
        self.get_roster()
        self.plugin['xep_0045'].joinMUC(self._muc_room,
                                        self._muc_nick,
                                        wait=True)

    def end(self, event):
        logger.debug('Cancelling timer')
        self._timer.cancel()

    def muc_got_online(self, presence):
        dirpath = os.path.dirname(os.path.realpath(__file__))
        filepath = os.path.join(dirpath, self._nicks_filename)
        with open(filepath, 'r+b') as f:
            nick = presence['muc']['nick']
            nicks = pickle.load(f)
            nicks.add(nick)
            f.seek(0)
            if nick not in nicks:
                logger.debug('Adding %s to %s', nick, self._nick_filename)
            pickle.dump(nicks, f)

    def message(self, msg):
        body = msg['body']
        if msg['type'] == 'groupchat' and body.startswith(self._CMD_PREFIX):
            cmd_args = body.strip().split(' ')
            # Strip command prefix e.g. !foo bar => foo
            cmd = cmd_args[0][len(self._CMD_PREFIX):]
            if cmd not in self.commands:
                logger.warning('Invalid command "%s"', cmd)
                return
            mtype, resp = self.commands[cmd](msg, *cmd_args[1:])
            msg_from = msg['from']
            if mtype == 'chat':
                mto = msg_from
            else:
                mto = msg_from.bare
            # Send help always as normal chat
            # if cmd == 'help':
            #     self.send_message(mto=msg_from,
            #                       mbody=resp,
            #                       mtype='chat')
            # else:
            self.send_message(mto=mto,
                              mbody=resp,
                              mtype=mtype)

    def _help(self, msg, *args):
        """Returns a help string containing all commands"""
        msg_type = msg['type']
        # MUC provides more commands as normal chat
        if msg_type in ('normal', 'chat'):
            cmds = self._cmds
        elif msg_type == 'groupchat':
            cmds = self._muc_cmds
        docs = []
        if args:  # help <command>
            cmd = args[0]
            if len(args) > 1 or cmd not in cmds:
                return
            doc = inspect.getdoc(cmds[cmd])
            docs.append(doc)
        else:  # help
            docs.append('Available commands:{}'.format(os.linesep))
            for cmd in sorted(cmds.keys()):
                doc = inspect.getdoc(cmds[cmd])
                if cmd == 'help' or not doc:
                    continue
                lines = doc.splitlines()
                docs.append('{}{}: {}'.format(self._CMD_PREFIX, cmd, lines[0]))
            bottom = ('{0}Type !help <command name> to get more info '
                      'about that specific command.').format(os.linesep)
            docs.append(bottom)
        src = 'Source code available at http://kurzma.ch/botsrc'
        docs.append(src)
        return os.linesep.join(docs)

    def _vote_start(self, msg, *args):
        """Starts a voting

You have to provide a subject: vstart <subject>
        """
        if self._vote_subject:
            return 'A vote is already running'
        if not args:
            return 'No subject given. Use vstart <subject>'
        self._vote_subject = ' '.join(args)
        return 'Voting started'

    def _vote_up(self, msg, *args):
        """Vote up for the current voting"""
        if not self._vote_subject:
            return self._NO_VOTINGS_MESSAGE
        user = msg['from'].resource
        if user in self._votes_up:
            return 'You already voted {}'.format(user)
        if user in self._votes_down:
            self._votes_down.remove(user)
        self._votes_up.add(user)
        return '{} voted up'.format(user)

    def _vote_down(self, msg, *args):
        """Vote down for the current voting"""
        if not self._vote_subject:
            return self._NO_VOTINGS_MESSAGE
        user = msg['from'].resource
        if user in self._votes_down:
            return 'You already voted down'
        if user in self._votes_up:
            self._votes_up.remove(user)
        self._votes_down.add(user)
        return '{} voted down'.format(user)

    def _vote_stat(self, msg, *args):
        """Displays statistics for the current voting"""
        if self._vote_subject:
            return 'Subject: "{}". Votes up: {:d}. Votes down: {:d}'.format(
                self._vote_subject,
                len(self._votes_up),
                len(self._votes_down))
        return self._NO_VOTINGS_MESSAGE

    def _vote_end(self, msg, *args):
        """Ends the current voting and shows the result"""
        if not self._vote_subject:
            return self._NO_VOTINGS_MESSAGE
        result = 'Voting "{}" ended. {:d} votes up. {:d} votes down'.format(
            self._vote_subject,
            len(self._votes_up),
            len(self._votes_down))
        self._vote_subject = None
        self._votes_up.clear()
        self._votes_down.clear()
        return result

    def _slap(self, msg, *args):
        """Slaps the given user

Simply type: !slap <nick> an it will slap the person
        """
        nick = ' '.join(args)
        if not nick:
            return 'You have to provide a nick name'
        dirpath = os.path.dirname(os.path.realpath(__file__))
        filepath = os.path.join(dirpath, 'slaps.txt')
        with open(filepath) as f:
            slaps = tuple(slap.strip() for slap in f)
            slap = random.choice(slaps).format(nick=nick)
            return '/me {}'.format(slap)

    def _meal(self, msg, *args):
        """Displays a 'enjoy your meal' message in a random language"""
        rand_lang = self._get_random_lang()
        meal = 'Enjoy your meal'
        translator = Translator(self._trans_client_id, self._trans_client_sec)
        translated = translator.translate(meal, rand_lang[0])
        return '{} (translated to {})'.format(translated, rand_lang[1])

    def _hug(self, msg, *args):
        """Hugs the given user"""
        if args:
            return '/me hugs {}'.format(' '.join(args))
        else:
            return 'Who should I hug?'

    def _kiss(self, msg, *args):
        """Kisses the given user

You can optionally specify the part of the body: \
kiss <nick> <part of body>
        """
        args_len = len(args)
        if not args:
            return 'Who should I kiss?'
        if args_len == 1:
            return '/me kisses {} :-*'.format(args[0])
        elif args_len == 2:
            return '/me kisses {} on the {} :-*'.format(args[0], args[1])
        else:
            return 'Too many arguments'

    def _wikipedia(self, msg, *args):
        """Displays a random page from the german Wikipedia

You can display today's featured article: wiki today
        """
        if 'today' in args:
            url = ('https://de.wikipedia.org/w/api.php'
                   '?action=featuredfeed&feed=featured')
            feed = feedparser.parse(url)
            today = feed['items'][-1]
            return self._shorten_url(msg, today['link'])
        params = {'action': 'query',
                  'format': 'json',
                  'generator': 'random',
                  'grnnamespace': 0,
                  'grnlimit': 1,
                  'prop': 'info',
                  'inprop': 'url'}
        req = requests.get('http://de.wikipedia.org/w/api.php', params=params)
        json = req.json()
        pages = json['query']['pages']
        page = list(pages.values())[0]
        url = self._shorten_url(msg, page['fullurl'])
        return '{}'.format(url)

    def _birthday(self, msg, *args):
        """Sends a happy birthday in an random language greeting

You can add a nickname: bday <nick>
        """
        rand_lang = self._get_random_lang()
        greet = 'Happy birthday to you'
        translator = Translator(self._trans_client_id, self._trans_client_sec)
        translated = translator.translate(greet, rand_lang[0])
        if args:
            return '{} @{} (translated to {})'.format(translated,
                                                      ' '.join(args),
                                                      rand_lang[1])
        return '{} (translated to {})'.format(translated, rand_lang[1])

    def _get_random_lang(self):
        dirpath = os.path.dirname(os.path.realpath(__file__))
        filepath = os.path.join(dirpath, 'lang_codes.txt')
        with open(filepath) as f:
            lines = [tuple(line.strip().split(';')) for line in f]
            langs = dict(lines)
        lang_code = random.choice(list(langs))
        country = langs[lang_code]
        return (lang_code, country)

    def _change_subject(self):
        """Changes randomly the subject of the MUC"""
        dirpath = os.path.dirname(os.path.realpath(__file__))
        filepath = os.path.join(dirpath, self._nicks_filename)
        with open(filepath, 'rb') as f:
            nicks = pickle.load(f)
        if nicks:
            nick = random.choice(list(nicks))
            subject = '{} ist ein Hengst'.format(nick)
            logger.debug('Changing MUC subject to "%s"', subject)
            self.send_message(mto=self._muc_room,
                              mbody=None,
                              msubject=subject,
                              mtype='groupchat')
        interval = random.randint(3600, 43200)
        logger.debug('Next MUC subject change in %d seconds', interval)
        self._timer = Timer(interval, self._change_subject)
        self._timer.start()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('jid',
                        help='the JID of the bot')
    parser.add_argument('pwd',
                        help='the password for the given JID')
    parser.add_argument('muc_room',
                        help='the MUC room to join')
    parser.add_argument('muc_nick',
                        help='the nick name that should be used')
    parser.add_argument()
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG,
                        format='%(levelname)-8s %(message)s')
    bot = MUCBot(args.jid, args.pwd, args.muc_room, args.muc_nick,
                 args.trans_client_id, args.trans_client_sec)
    bot.connect()
    bot.process(block=True)
