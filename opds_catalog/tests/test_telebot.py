import logging
from collections import OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import TransactionTestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.management.commands.sopds_telebot import Command


class TelegramCommandTests(TransactionTestCase):
    def setUp(self):
        self.command = Command()
        self.command.logger = logging.getLogger('telegram-test')
        self.command.query_cache = OrderedDict()
        config.SOPDS_TELEBOT_AUTH = False
        config.SOPDS_LANGUAGE = 'en-US'

    def update(self, text):
        message = SimpleNamespace(
            text=text,
            chat_id=123,
            from_user=SimpleNamespace(username='reader'),
        )
        return SimpleNamespace(message=message, callback_query=None)

    def test_search_uses_orm_from_async_callback(self):
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook('title.fb2', '.', catalog, 'fb2', 'Test Title', '', '', 'en')
        bot = SimpleNamespace(send_message=AsyncMock())

        async_to_sync(self.command.getBooks)(self.update('Test'), SimpleNamespace(bot=bot))

        self.assertEqual(bot.send_message.await_count, 3)
        self.assertIn(f'/download{book.pk}', bot.send_message.await_args.kwargs['text'])

    def test_file_handle_closes_after_send(self):
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook('title.fb2', '.', catalog, 'fb2', 'Test Title', '', '', 'en')
        bot = SimpleNamespace(send_document=AsyncMock())
        with TemporaryDirectory() as root:
            Path(root, 'title.fb2').write_bytes(b'artificial book')
            config.SOPDS_ROOT_LIB = root
            callback = SimpleNamespace(
                data=f'/getfileorig{book.pk}',
                message=SimpleNamespace(chat_id=123),
                from_user=SimpleNamespace(username='reader'),
            )
            update = SimpleNamespace(message=None, callback_query=callback)
            async_to_sync(self.command.getBookFile)(update, SimpleNamespace(bot=bot))

        bot.send_document.assert_awaited_once()
        document = bot.send_document.await_args.kwargs['document']
        self.assertTrue(document.closed)

    def test_download_button_uses_existing_book_id(self):
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook('title.fb2', '.', catalog, 'fb2', 'Test Title', '', '', 'en')
        bot = SimpleNamespace(send_message=AsyncMock())

        async_to_sync(self.command.downloadBooks)(
            self.update(f'/download{book.pk}'), SimpleNamespace(bot=bot)
        )

        bot.send_message.assert_awaited_once()
        buttons = bot.send_message.await_args.kwargs['reply_markup'].inline_keyboard[0]
        self.assertEqual(buttons[0].callback_data, f'/getfileorig{book.pk}')

    def test_download_details_hide_legacy_annotation_marker(self):
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook('title.fb2', '.', catalog, 'fb2',
                              'Test Title', 'NotYet', '', 'en')
        details = self.command.book_download_details(book.pk)
        self.assertNotIn('NotYet', details[0])

    def test_authorization_blocks_unknown_user(self):
        config.SOPDS_TELEBOT_AUTH = True
        bot = SimpleNamespace(send_message=AsyncMock())
        update = self.update('/start')

        async_to_sync(self.command.startCommand)(update, SimpleNamespace(bot=bot))
        self.assertIn('do not have access', bot.send_message.await_args.kwargs['text'])

        User.objects.create_user('reader', password='test-password')
        bot.send_message.reset_mock()
        async_to_sync(self.command.startCommand)(update, SimpleNamespace(bot=bot))
        self.assertIn('To search for a book', bot.send_message.await_args.kwargs['text'])
