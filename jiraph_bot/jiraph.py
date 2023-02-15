import asyncio
import datetime
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import reduce
from itertools import zip_longest
from typing import Callable

import aiohttp
import emoji
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError

from jiraph_bot import slack_views
from jiraph_bot.jira_async import JiraAsync, Response


class Jiraph:
    @dataclass
    class FieldOption:
        type: str
        project: str
        issuetype: str
        field: str

    @dataclass
    class ViewMetaData:
        channel: str
        thread_ts: str
        message_ts: str

    def __init__(
            self,
            slack_bot_token: str = None
    ):
        self.slack_app = AsyncApp(token=slack_bot_token)
        self.app_name = os.environ.get('APP_NAME') or "Jiraph"
        self.required_fields = ('priority', 'assignee')
        self.default_fields = ('summary', 'description', 'project', 'issuetype')
        self.allowed_jira_img = ('bmp', 'dcm', 'gif', 'heif', 'heic', 'jpg', 'jpeg', 'png', 'psd', 'tif', 'tiff')
        self.emoji = emoji.get_aliases_unicode_dict()
        self.jira = JiraAsync()
        self.jira_comment_limit = 31000
        self.__init_jiraph_functions()

    def __init_jiraph_functions(self):
        # Shortcuts
        self.slack_app.shortcut("jira-issue-shortcut", middleware=[self.no_direct, self.check_access]) \
            (self.issue_from_thread_shortcut)
        self.slack_app.shortcut("attach-thread-shortcut", middleware=[self.no_direct, self.check_access]) \
            (self.thread_to_issue_shortcut)

        # Options
        self.slack_app.options('choose_project') \
            (self.projects_options)
        self.slack_app.options('find_issue') \
            (self.issues_options)
        self.slack_app.options('choose_priority') \
            (self.priorities_options)
        self.slack_app.options(re.compile('field.')) \
            (self.fields_options)

        # Actions
        self.slack_app.action('choose_project') \
            (self.choose_project_action)
        self.slack_app.action('issue_type') \
            (self.choose_issuetype_action)

        # Views
        self.slack_app.view('issue-from-thread') \
            (self.issue_from_thread_view_submit_handle)
        self.slack_app.view('attach-to-issue') \
            (self.thread_to_issue_view_submit_handle)

    def __bot_name(self):
        loop = asyncio.get_event_loop()
        auth_test = loop.run_until_complete(self.slack_app.client.auth_test())
        return auth_test.data.get('bot_id')


    async def download_file(
            self,
            file_url: str
    ) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url,
                                   headers={'Authorization': 'Bearer ' + self.slack_app.client.token},
                                   raise_for_status=True) as response:
                return await response.read()

    async def get_user_name(
            self,
            user_id: str
    ) -> str:
        user = await self.slack_app.client.users_info(user=user_id)
        return user['user']['name']

    async def get_thread_link(
            self,
            channel: str,
            thread: str
    ) -> str:
        link = await self.slack_app.client.chat_getPermalink(
            channel=channel,
            message_ts=thread
        )
        return link.data['permalink']

    def attaches_to_comment(
            self,
            message: dict
    ) -> str:
        if message.get('files'):
            attaches = []
            for file in message['files']:
                if file.get('file_access', '') == 'file_not_found':
                    continue
                if file['name'].endswith(self.allowed_jira_img):
                    attaches.insert(0, '!' + file['id'] + file['name'] + '|thumbnail!')
                else:
                    attaches.append('\n[^' + file['id'] + file['name'] + ']')
            return ' '.join(attaches)
        return ''

    @staticmethod
    async def format_jira_text(elem: dict) -> str:
        text = elem['text'].strip(' ')
        style = elem.get('style')
        jira_styles = []
        code = False
        if style:
            if style.get('bold'):
                jira_styles.append('*')
            if style.get('italic'):
                jira_styles.append('_')
            if style.get('strike'):
                jira_styles.append('-')
            if style.get('code'):
                code = True
        if jira_styles:
            text = ''.join(jira_styles) + text + ''.join(jira_styles[::-1])
        if code:
            text = '{quote}{{{color:#DE350B}' + text + '{color}}}{quote}'
        return text

    async def format_elem(
            self,
            elem: dict
    ) -> str:
        match elem['type']:
            case 'text':
                return await self.format_jira_text(elem)
            case 'link':
                if elem.get('text'):
                    return f'[{await self.format_jira_text(elem)}|{elem["url"]}]'
                else:
                    return elem['url']
            case 'user':
                return f'[~{await self.get_user_name(elem["user_id"])}]'
            case 'emoji':
                return self.emoji.get(f':{elem["name"]}:', f':{elem["name"]}:')
            case 'broadcast':
                return f'@{elem["range"]}'
            case _:
                return ''

    async def message_elems_format(
            self,
            elems: dict
    ) -> str:
        elem_type = elems['type']
        text = ''
        match elem_type:
            case 'rich_text_section':
                text += ' '.join(await asyncio.gather(*(self.format_elem(e) for e in elems['elements'])))
            case 'rich_text_list':
                if elems['style'] == 'ordered':
                    text += '\n# ' + '\n# '.join(await asyncio.gather(*(self.format_elem(e['elements'][0])
                                                                        for e in elems['elements'])))
                elif elems['style'] == 'bullet':
                    text += '\n* ' + '\n* '.join(await asyncio.gather(*(self.format_elem(e['elements'][0])
                                                                        for e in elems['elements'])))
            case 'rich_text_quote':
                text += \
                    '{quote}' + \
                    " ".join(await asyncio.gather(*(self.format_elem(e) for e in elems["elements"]))) + \
                    '{quote}}'
            case 'rich_text_preformatted':
                text += \
                    '{code}' + \
                    " ".join(await asyncio.gather(*(self.format_elem(e) for e in elems["elements"]))) + \
                    '{code}'
        return text

    def slice_comment_limit(
            self,
            comments
    ) -> Iterator[str]:
        limited_comment = ''
        for i, comment in enumerate(comments):
            if len(limited_comment + comment) >= self.jira_comment_limit:
                yield limited_comment
                limited_comment = ''
            elif i + 1 == len(comments):
                limited_comment += comment
                yield limited_comment
                limited_comment = ''
            limited_comment += comment

    async def parse_comment(
            self,
            comment: dict,
            channel: str
    ) -> str:
        message_link, user_name = await asyncio.gather(
            self.slack_app.client.chat_getPermalink(
                channel=channel,
                message_ts=comment['ts']
            ),
            self.get_user_name(comment["user"])
        )
        if comment.get('blocks', [{}])[0].get('elements'):
            comment_elements = comment['blocks'][0]['elements']
            parsed_comment = ' '.join(
                await asyncio.gather(*(self.message_elems_format(elem) for elem in comment_elements)))
        else:
            parsed_comment = comment.get('text', '')

        attaches_text = self.attaches_to_comment(comment) if comment.get('files') else ''

        return f'\n---- \n??[~{user_name}]?? ' \
               f'[{{{{{str(datetime.datetime.fromtimestamp(float(comment["ts"])))}}}}}|' \
               f'{message_link["permalink"]}]' \
               f'\n\n {parsed_comment} \n {attaches_text} \n'

    async def parse_and_send_comments(
            self,
            issue_key: str,
            channel: str,
            thread_link: str,
            new_issue: bool,
            comments: tuple
    ) -> Callable[[str], str]:
        comments = await asyncio.gather(*(self.parse_comment(comment, channel) for comment in comments))
        comments_sep_limit = list(self.slice_comment_limit(comments))
        comments_tasks = []
        updated = False
        if new_issue:
            for comment in comments_sep_limit:
                comments_tasks.append(self.jira.add_comment(issue_key=issue_key,
                                                            comment=thread_link + comment))
        else:
            jira_comments = await self.jira.get_issue_comments(issue_key=issue_key)
            jira_comments = tuple(filter(lambda x: x.body.startswith(thread_link) and
                                                   x.author.name == os.environ.get('JIRA_USER'),
                                         jira_comments))
            zipped_comments = zip_longest(comments_sep_limit, jira_comments, fillvalue=None)
            for parsed_comment, jira_comment in zipped_comments:
                if parsed_comment and jira_comment:
                    comments_tasks.append(
                        self.jira.edit_comment(
                            issue_key=issue_key,
                            comment_id=jira_comment.id,
                            comment=thread_link + parsed_comment
                        )
                    )
                    updated = True
                elif parsed_comment and not jira_comment:
                    comments_tasks.append(
                        self.jira.add_comment(
                            issue_key=issue_key,
                            comment=thread_link + parsed_comment
                        )
                    )
                elif not parsed_comment and jira_comment:
                    updated = True
                    comments_tasks.append(
                        self.jira.delete_comment(
                            issue_key=issue_key,
                            comment_id=jira_comment.id
                        )
                    )
        tasks = await asyncio.gather(*comments_tasks)
        issue_link = f'<{self.jira.base_url}/browse/{issue_key}|{issue_key}>'
        if updated:
            return lambda user: f'User <@{user}> updated comments from this thread in {issue_link}'
        else:
            return lambda user: f'User <@{user}> sent thread to {issue_link}'

    async def upload_attachment(
            self,
            issue_key: str,
            file_url: str,
            file_name: str
    ) -> Response:
        file = await self.download_file(file_url=file_url)
        return await self.jira.upload_attachment(issue_key=issue_key,
                                                 filename=file_name,
                                                 file=file)

    async def upload_attachments(
            self,
            issue_key: str,
            comments: tuple
    ) -> None:
        already_attached = (attach.filename for attach in await self.jira.get_issue_attachments(issue_key))
        slack_files = (file for comment in comments for file in comment.get('files', []))
        files = tuple(filter(lambda file: file.get('id', '') + file.get('name', '') not in already_attached
                                          and file.get('file_access') != 'file_not_found', slack_files))
        await asyncio.gather(
            *(
                self.upload_attachment(
                    issue_key,
                    file['url_private_download'],
                    file['id'] + file['name']
                ) for file in files)
        )

    async def upload_thread(
            self,
            issue_key: str,
            channel: str,
            thread: str,
            new_issue: bool,
            user: str
    ) -> None:
        comments, thread_link, _ = await asyncio.gather(
            self.slack_app.client.conversations_replies(
                channel=channel,
                ts=thread
            ),
            self.get_thread_link(
                channel=channel,
                thread=thread
            ),
            self.slack_app.client.chat_postEphemeral(channel=channel,
                                                     thread_ts=thread,
                                                     user=user,
                                                     text='Parsing thread started')
        )
        comments = tuple(filter(lambda x: not x.get('bot_id'), comments.data['messages']))
        message_func, _ = await asyncio.gather(
            self.parse_and_send_comments(
                issue_key=issue_key,
                channel=channel,
                comments=comments,
                thread_link=thread_link,
                new_issue=new_issue
            ),
            self.upload_attachments(
                issue_key=issue_key,
                comments=comments
            )
        )
        await self.slack_app.client.chat_postEphemeral(channel=channel,
                                                       thread_ts=thread,
                                                       user=user,
                                                       text='Parsing thread completed')
        return message_func

    @staticmethod
    async def no_direct(
            ack,
            respond,
            body,
            next
    ):
        if body['channel']['name'] == 'directmessage':
            await asyncio.gather(
                ack(),
                respond(
                    'Sorry, this shortcut unavailable in direct messages',
                    response_type='ephemeral'
                )
            )
        else:
            await next()

    async def check_access(
            self,
            ack,
            client,
            body,
            next
    ):
        try:
            await client.conversations_replies(
                channel=body['channel']['id'],
                ts=body['message'].get('thread_ts', '') or body['message']['ts']
            )
            access = True
        except SlackApiError:
            access = False

        if not access:
            await asyncio.gather(ack(),
                                 client.views_open(trigger_id=body.get('trigger_id'),
                                                   view=slack_views.no_access_view(self.app_name)))
        else:
            await next()

    async def issue_from_thread_shortcut(
            self,
            ack,
            client,
            body,
    ):
        thread_ts = body['message'].get('thread_ts', '')
        await asyncio.gather(
            ack(),
            client.views_open(
                trigger_id=body.get('trigger_id'),
                view=slack_views.choose_project_view(
                    json.dumps({'channel': body['channel']['id'],
                                'thread_ts': thread_ts if thread_ts else body['message']['ts'],
                                'message_ts': body['message']['ts']}))
            )
        )

    # @staticmethod
    async def thread_to_issue_shortcut(
            self,
            ack,
            client,
            body
    ):
        thread_ts = body['message'].get('thread_ts', '')
        await asyncio.gather(
            ack(),
            client.views_open(
                trigger_id=body.get('trigger_id'),
                view=slack_views.choose_issue_view(
                    json.dumps({'channel': body['channel']['id'],
                                'thread_ts': thread_ts if thread_ts else body['message']['ts'],
                                'message_ts': body['message']['ts']}))
            )
        )

    async def projects_options(
            self,
            ack,
            payload
    ) -> None:
        search_key = payload.get('value', '').casefold()
        projects = await self.jira.get_projects()
        if search_key:
            projects = list(
                filter(lambda x: search_key in x.name.casefold() or search_key in x.key.casefold(), projects))
        await ack(
            options=[{'text': {"type": "plain_text", "text": f'{elem.key}: {elem.name}'[:75]}, 'value': elem.key}
                     for elem in projects[:100]])

    async def issues_options(
            self,
            ack,
            payload
    ) -> None:
        search_key = payload.get('value', '').upper()
        issues = await asyncio.gather(
            self.jira.search_by_jql(f'issuekey = {search_key}'),
            self.jira.search_by_jql(f'summary ~ "{search_key}"')
        )
        issues = issues[0].issues + issues[1].issues
        await ack(options=[{'text': {"type": "plain_text", "text": f'{issue.key}: {issue.fields.summary}'[:75]},
                            'value': issue.key}
                           for issue in issues]
                  )

    async def priorities_options(
            self,
            ack,
            payload
    ) -> None:
        search_key = payload.get('value', '')
        priorities = await self.jira.get_priorities()
        if search_key:
            priorities = list(filter(lambda x: search_key.casefold() in x['name'].casefold(), priorities))
        await ack(options=[{'text': {"type": "plain_text", "text": priority.name[:75]},
                            'value': priority.id}
                           for priority in priorities])

    async def choose_project_action(
            self,
            ack,
            body,
            client
    ) -> None:
        project = reduce(lambda x, y: x[y], slack_views.view_value_path('choose_project'), body)
        _, issuetypes = await asyncio.gather(
            ack(),
            self.jira.get_issuetypes(project_key=project)
        )
        issuetypes = [{'text': {"type": "plain_text", "text": issuetype.name[:75]},
                       'value': issuetype.id}
                      for issuetype in issuetypes.issuetypes if not issuetype.subtask]
        allowed_step_blocks = ['choose_project']
        view = slack_views.empty_view(body['view']['private_metadata'])
        view['blocks'].extend(
            list(filter(lambda block: block['block_id'] in allowed_step_blocks, body['view']['blocks'])))
        upd_resp = await client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=view
        )
        view['blocks'].append(slack_views.issuetypes_block(issuetypes))
        await client.views_update(
            view_id=body["view"]["id"],
            hash=upd_resp.data['view']['hash'],
            view=view
        )

    async def choose_issuetype_action(
            self,
            ack,
            body,
            client
    ) -> None:
        project = reduce(lambda x, y: x[y], slack_views.view_value_path('choose_project'), body)
        issuetype = reduce(lambda x, y: x[y], slack_views.view_value_path('issue_type'), body)
        _, create_meta = await asyncio.gather(ack(),
                                              self.jira.get_create_meta(project_key=project,
                                                                        issuetype=issuetype))

        fields = [next(filter(lambda field: field.fieldId == req_field, create_meta.fields), None)
                  for req_field in self.required_fields]
        fields.extend(list(
            filter(lambda field: field.required and
                                 field.fieldId not in self.default_fields and
                                 field.fieldId not in self.required_fields,
                   create_meta.fields)))
        allowed_step_blocks = ['choose_project', 'issue_type']
        view = slack_views.empty_view(body['view']['private_metadata'])
        view['blocks'].extend(list(filter(lambda block: block['block_id'] in allowed_step_blocks,
                                          body['view']['blocks'])))
        upd_resp = await client.views_update(
            view_id=body["view"]["id"],
            # hash=body["view"]["hash"],
            view=view)
        meta_data = json.loads(view['private_metadata'])
        view['submit'] = {'text': 'Submit', 'type': 'plain_text'}
        if meta_data['thread_ts']:
            message = await self.slack_app.client.conversations_replies(
                channel=meta_data['channel'],
                ts=meta_data['message_ts']
            )
        else:
            message = await self.slack_app.client.conversations_history(
                channel=meta_data['channel'],
                oldest=meta_data['message_ts'],
                limit=1,
                inclusive=True
            )
        message = message.data['messages'][0]
        parsed_message = await self.parse_comment(
            comment=message,
            channel=meta_data['channel']
        )
        view['blocks'].append(slack_views.summary(project, issuetype, message['text']))
        view['blocks'].append(slack_views.descrtiption(project, issuetype, parsed_message.strip()))
        for i in fields:
            if i:
                view['blocks'].append(slack_views.make_input_block(i, project, issuetype))
        await client.views_update(view_id=body["view"]["id"],
                                  # hash=upd_resp.data['view']['hash'],
                                  view=view)

    # Option
    async def fields_options(
            self,
            ack,
            payload,
    ) -> None:
        data = self.FieldOption(*payload['action_id'].split('.'))
        create_meta = await self.jira.get_create_meta(
            project_key=data.project,
            issuetype=data.issuetype
        )
        data = self.FieldOption(*payload['action_id'].split('.'))
        search_word = payload.get('value', '')
        field = next(filter(lambda field: field.fieldId == data.field, create_meta.fields))
        options = []
        if field.autoCompleteUrl and \
                (field.schema_.type == 'user' or field.schema_.items == 'user'):
            if search_word:
                results = await self.jira.find_user(search_word)
                options = [{"text": {"type": "plain_text", "text": value.displayName},
                            "value": f'{{"name": "{value.name}"}}'} for value in results]
        else:
            if field.allowedValues:
                allowed_values = field.allowedValues
                key = 'name' if field.allowedValues[0].get('name') else 'value'
                if search_word:
                    allowed_values = list(filter(lambda x: search_word in x[key].lower(), allowed_values))
                allowed_values = allowed_values[:100]
                options = [{"text": {"type": "plain_text", "text": value[key]},
                            "value": f'{{"id": "{value["id"]}"}}'}
                           for value in allowed_values]
        await ack(options=options)

    async def issue_from_thread_view_submit_handle(
            self,
            ack,
            body,
            client,
            view,
    ) -> None:
        summary = list(view['state']['values']['summary'].values())[0]['value']
        if len(summary) > 255:
            await ack(response_action="errors", errors={'summary': 'Summary longer than 255'})
            return
        meta = self.ViewMetaData(**json.loads(view['private_metadata']))
        chat = meta.thread_ts if meta.thread_ts else meta.message_ts
        state = view['state']['values']
        fields_values = {}
        for block in view['blocks']:
            block_state = state[block['block_id']][block['element']['action_id']]
            if block['block_id'] == 'choose_project':
                fields_values['project'] = {'key': block_state['selected_option']['value']}
            elif block['block_id'] == 'issue_type':
                fields_values['issuetype'] = {'id': block_state['selected_option']['value']}
            else:
                if block_state.get('type') == 'plain_text_input':
                    if block_state['value']:
                        fields_values[block['block_id']] = block_state['value']
                elif block_state.get('type') == 'external_select':
                    if block_state['selected_option']:
                        fields_values[block['block_id']] = json.loads(block_state['selected_option']['value'])
                elif block_state.get('type') == 'multi_external_select':
                    if block_state['selected_options']:
                        fields_values[block['block_id']] = [json.loads(opt['value'])
                                                            for opt in block_state['selected_options']]
        issue = await self.jira.create_issue(fields_values)

        if issue.errorMessages and issue.key:
            await asyncio.gather(ack(),
                                 self.slack_app.client.chat_postEphemeral(channel=meta.channel,
                                                                          thread_ts=chat,
                                                                          user=body['user']['id'],
                                                                          text='Issue created with errors:\n' +
                                                                               '\n'.join(issue.errorMessages)),
                                 self.slack_app.client.chat_postMessage(
                                     text=f"User <@{body['user']['username']}> created issue from this thread. \n"
                                          f"Issue: <{self.jira.base_url}/browse/{issue.key}|{issue.key}>",
                                     thread_ts=chat,
                                     channel=meta.channel
                                 ),
                                 self.upload_thread(
                                     issue_key=issue.key,
                                     channel=meta.channel,
                                     thread=meta.thread_ts,
                                     user=body['user']['id'],
                                     new_issue=True))
        elif not issue.key:
            await asyncio.gather(ack(),
                                 self.slack_app.client.chat_postEphemeral(channel=meta.channel,
                                                                          thread_ts=chat,
                                                                          user=body['user']['id'],
                                                                          text='Issue has not been created cause:\n' +
                                                                               '\n'.join(f"{key}:{value}"
                                                                                         for key, value in
                                                                                         issue.errors.items())))
        else:
            await asyncio.gather(ack(),
                                 self.slack_app.client.chat_postMessage(
                                     text=f"User <@{body['user']['username']}> created issue from this thread. \n"
                                          f"Issue: <{self.jira.base_url}/browse/{issue.key}|{issue.key}>",
                                     thread_ts=chat,
                                     channel=meta.channel
                                 ),
                                 self.upload_thread(
                                     issue_key=issue.key,
                                     channel=meta.channel,
                                     thread=meta.thread_ts,
                                     user=body['user']['id'],
                                     new_issue=True))

    async def thread_to_issue_view_submit_handle(
            self,
            ack,
            body,
            client,
            view,
    ):
        meta = self.ViewMetaData(**json.loads(view['private_metadata']))
        chat = meta.thread_ts if meta.thread_ts else meta.message_ts
        issue_key = view['state']['values']['find_issue']['find_issue']['selected_option']['value']
        _, message_func = await asyncio.gather(
            ack(),
            self.upload_thread(
                issue_key=issue_key,
                channel=meta.channel,
                thread=meta.thread_ts,
                user=body['user']['id'],
                new_issue=False
            )
        )
        await client.chat_postMessage(
            text=message_func(body['user']['username']),
            thread_ts=chat,
            channel=meta.channel
        )

    async def start(self):
        handler = AsyncSocketModeHandler(app=self.slack_app,
                               app_token=os.environ.get('SLACK_APP_TOKEN'))
        await handler.start_async()


