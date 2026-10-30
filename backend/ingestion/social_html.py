# @file: backend/ingestion/social_html.py
# @exports: TelegramParser, NextDataParser
# @depends_on: html.parser
# @purpose: Dependency-free extraction of public Telegram messages and X timeline JSON scripts.
from html.parser import HTMLParser


class NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active, self.parts = False, []

    def handle_starttag(self, tag, attrs):
        self.active = tag == 'script' and dict(attrs).get('id') == '__NEXT_DATA__'

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


class TelegramParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.messages, self.current, self.depth, self.text_depth = [], None, 0, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in ('br', 'img', 'hr', 'meta', 'input', 'link'):
            self.depth += 1
        if attrs.get('data-post'):
            self.current = {'post': attrs['data-post'], 'text': '', 'stamp': None}
            self.messages.append(self.current)
        if self.current and 'tgme_widget_message_text' in attrs.get('class', '').split():
            self.text_depth = self.depth
        if self.current and tag == 'time':
            self.current['stamp'] = attrs.get('datetime')
        if tag == 'br' and self.text_depth:
            self.current['text'] += '\n'

    def handle_endtag(self, tag):
        if self.text_depth == self.depth:
            self.text_depth = None
        self.depth = max(0, self.depth-1)

    def handle_data(self, data):
        if self.current and self.text_depth:
            self.current['text'] += data
