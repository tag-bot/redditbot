import os
import time
import praw
import re
import logging as log
from time import sleep
from itertools import groupby

#TODO:

# - error messages when config is missing

log.basicConfig(level=log.DEBUG)

re_user = re.compile('/u/([^\s]*)')
re_subreddit = re.compile('/r/([^/]*)')
re_locked = re.compile('\* ([^\s]*)')
re_list = re.compile('\* [^\n]*')
re_name = re.compile('\[(.*)\]')
re_title = re.compile('\[(oc|pi|jenkinsverse|j-verse|jverse|misc|nsfw)\]', re.IGNORECASE)
re_perm = re.compile('\((http[^)]*)\)')


# expected format is: "* [title](link) - by: [author](link-to-authors-wiki)"
class SortableLine:
    def __init__(self, line):
        self.title_md = re_title.sub('', line).strip() + '\n\n'

        try:
            self.name = re.findall(re_name, line)
            self.name = re_title.sub('', self.name[0]).strip()

            self.permalink = re_perm.findall(line)
            self.permalink= self.permalink[0]

            self.sortby = self.name.lower()
        except Exception, e:
            log.exception('Incorrect format!')
            self.sortby = line
            self.name = line


def sort_titles(titles):
    keys = []
    groups = []

    for k, g in groupby(titles, lambda x: x.sortby):
        keys.append(k)
        groups.append(list(g))

    # first element of every group should hold correctly capitalized title
    
    return [x[0] for x in sorted(groups, key=lambda x: x[0].sortby)]

def create_wiki_page(lines, tag):
    anchor = None
    if tag.startswith('-'): tag = tag[1:]
    ret = []
    ret.append('#%s' % tag)
    ret.append('\n\n')

    # add anchors based on first letter of the name
    for line in lines:
        if line.name[0].lower() != anchor:
            anchor = line.name[0].lower()
            ret.append('\n\n')
            ret.append('##%s' % anchor.upper())
            ret.append('\n\n')
        ret.append(line.title_md)

    return "".join(ret)


class TagBot:
    def __init__(self, subreddit):
        self.subreddit = subreddit
        self.last_seen = 0
        
        self._account = praw.Reddit(user_agent='redditbot 0.1 by /u/HFY_tag_bot')
        self._account.login(os.environ['REDDIT_USER'], os.environ['REDDIT_PASS'])

        self.wiki_modification_time = {}

        self.read_config()

    def account(self, sleep_time=5):
        sleep(sleep_time)
        return self._account

    def read_config(self):
        try:
            self.tags = [ x.lower() for x in self.get_accepted_tags() ]

            for t in self.tags:
                if t not in self.wiki_modification_time: self.wiki_modification_time[t] = 0

            self.volunteers = self.get_volunteers()
            self.mods = self.get_mods()
            self.codex_keeper = self.get_codex_keeper().replace('/u/','').replace('/','')
            self.read_locked()
        except Exception, e:
            log.exception("Unable to read config file! retrying in 30s")
            sleep(30) 

    def get_codex_keeper(self):
        return re_user.findall(self.get_wiki_page('codexkeeper').content_md)[0]

    def get_mods(self):
        return [x.name for x in self.account().get_subreddit(self.subreddit).get_moderators()]

    def get_volunteers(self):
        return re_user.findall(self.get_wiki_page('volunteers').content_md)
    
    def get_accepted_tags(self):
        return re_name.findall(self.get_wiki_page('accepted').content_md)

    def has_new_tags(self, comment):
        return comment.body.startswith('tags:') \
               and comment.created > self.last_seen \
               and not comment.edited

    def update_wiki_page(self, comment):
        reply = ''

        if comment.submission.url in self.locked:
            comment.reply("This submission is no longer accepting tags")
            return

        tmp = comment.body.replace(",", " ").replace('tags:', '')
        added = [ x.title() for x in tmp.split() if x.lower() in self.tags ]
        removed = [ x[1:].title() for x in tmp.split() if  x.startswith('-') and re.sub(r'^-','',x).lower() in self.tags ]

        log.debug("found tags: %s" % ",".join(added + removed))

        if comment.author.name != comment.submission.author.name and comment.author.name not in self.mods:
            removed = []
            reply += 'Only the submitter or one of the mods can remove tags! sorry!\n\n'

        for tag in added + removed:
            page = self.get_wiki_page(tag)
            lines = [ SortableLine(line) for line in re.findall(re_list, page.content_md) if tag not in removed ]
            if tag not in removed:
                lines += [ SortableLine('* [%s](%s) - by: [%s](/r/%s/wiki/%s)\n\n' % (comment.submission.title, 
                                                                                      comment.submission.permalink, 
                                                                                      comment.submission.author.name, 
                                                                                      self.subreddit,
                                                                                      comment.submission.author.name)) ]
            log.debug("updating %s [removing?: %s] for %s" % (tag, tag in removed, comment.submission.title))
            md = create_wiki_page(sort_titles(lines), tag)
            page.edit(md)

        
        links = [ "[%s](/r/%s/wiki/tags/%s)" % (tag.title(), self.subreddit, tag.title()) for tag in added]
        if links: reply = "Verified tags: %s" % ", ".join(links)

        reply += '\n\n'

        if removed: reply += "Removed tags: %s" % ", ".join(removed)

        reply += '\n\nAccepted list of tags can be found here: /r/HFYBeta/wiki/tags/accepted'
        comment.reply(reply)


    def edit_wiki_page(self, tag, text):
        log.debug('updating wiki page %s' % (self.subreddit + '/tags/'+tag,))
        self.account().edit_wiki_page(self.subreddit, 'tags/'+tag, text)

    def get_comments(self):
        return self.account().get_comments(self.subreddit, limit=50)

    def get_wiki_page(self, tag):
        try:
            return self.account().get_wiki_page(self.subreddit, 'tags/'+tag)
        except:
            log.exception('No such page?')

    def save_last_seen(self):
        return self.account().edit_wiki_page(self.subreddit, 'tags/last_seen', self.last_seen)

    def get_last_seen(self):
        return self.account().get_wiki_page(self.subreddit, 'tags/last_seen')

    def send_message(self, recipient, subject, message):
        try:
            return self.account().send_message(recipient, subject, message, raise_captcha_exception=True)
        except Exception, e:
            log.exception('Captcha exception?')

    def verify_user(self, comment):
        if comment.author.name not in self.volunteers + [comment.submission.author.name] + self.mods:
            log.debug("Unauthorized tagging attempt")
            comment.reply("You need to contact /u/%s  to be able to volunteer tags!" % self.codex_keeper)
            return False
        else:
            return True

    def get_submission(self, msg):

        try:
            submission = self.account().get_submission(msg.subject)
            subreddit = re_subreddit.findall(submission.permalink)

            if subreddit and subreddit[0] == self.subreddit:  return submission

            log.debug('got message with subject %s for bot configured on subreddit %s' % (permalink, self.subreddit))
            msg.reply("I'can only work on %s this is a submission to %s" % (self.subreddit, subreddit))
        except Exception, e:
            log.exception('Not a submission?')
            msg.reply("I'm sorry i can't seem to get submision from url: %s\n\nYou will have to try again :(\n\n(Error: %s)" % msg.subject, e.message)
            msg.mark_as_read()

    def check_messages(self):
        messages = list(self.account().get_unread())

        log.debug('checking messages')

        for msg in messages:
            if msg.subject == 'reload':
                if  msg.author.name not in self.mods:
                    msg.mark_as_read()
                    msg.reply("Nice try, but you're not a mod ;)")

                self.read_config()
                msg.reply("Settings have been reloaded")
                msg.mark_as_read()
                continue

            submission = self.get_submission(msg)
            log.debug('checking %s' % msg.subject)
            if not submission:
                msg.mark_as_read()
                log.debug('discarding')
                continue

            log.debug('message subject %s' % msg.subject)
            msg.submission = submission

            if msg.body.startswith('tags:'):
                self.update_wiki_page(msg)
                msg.mark_as_read()

            if msg.body.startswith('lock:'):
                if msg.author.name != submission.author.name and msg.author.name not in self.mods:
                    msg.mark_as_read()
                    msg.reply('Only author or mod can lock a thread')

                else:
                    content = ''
                    locked = self.get_wiki_page('locked')
                    if locked: content = locked.content_md

                    content += '* %s' % submission.url
                    content += '\n\n'

                    content = content.split('* ')
                    content = ''.join(["* %s" % x for x in sorted(set(content)) if x])
                    
                    self.update_wiki_page(msg)
                    self.edit_wiki_page('locked', content)
                    msg.mark_as_read()
                    msg.reply("The submission tags can no longer be changed by volunteers")


            log.debug("discarding")
            msg.mark_as_read()

    def read_locked(self):
        locked = self.get_wiki_page('locked')
        self.locked = re.findall(re_locked, locked.content_md)
        
    def run(self):
        config_counter = 0

        while True:
            log.debug('waking up')
            self.last_seen = float(self.get_last_seen().content_md)
            comments = self.get_comments()

            try:
                for tag_comment in  [ x for x in comments if self.has_new_tags(x) ]:
                    log.debug('Processing comment %s' % tag_comment.permalink)
                    if self.verify_user(tag_comment): 
                        self.update_wiki_page(tag_comment)

                    if tag_comment.created > self.last_seen:
                        self.last_seen = tag_comment.created

                self.check_messages();
            finally:
                self.save_last_seen()

            log.debug('sleeping...')
            sleep(30)

            if config_counter == 5:
                self.read_config()
                conifg_counter = 0


def main():
    while True:
        try:
            TagBot('HFYBeta').run()
        except Exception, e:
            log.exception(e)
            sleep(140)

if __name__ == '__main__':
    main()        
