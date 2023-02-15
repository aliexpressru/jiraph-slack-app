import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from os import environ
from typing import Optional, Any

import aiohttp
from pydantic import BaseModel, Field


class CustomModel(BaseModel):
    errorMessages: Optional[list[str]] = []

    def __bool__(self):
        if self.errorMessages:
            return False
        return True


class Author(CustomModel):
    name: str
    key: str
    displayName: str


class Attachment(CustomModel):
    id: str
    filename: str


class Comment(CustomModel):
    id: str
    body: str
    author: Author


class CommentField(CustomModel):
    comments: Optional[list[Comment]] = []


class Fields(CustomModel):
    attachment: Optional[list[Attachment]] = []
    comment: Optional[CommentField]
    summary: Optional[str]


class IssueType(CustomModel):
    id: str
    name: str
    subtask: bool


class Issuetypes(CustomModel):
    issuetypes: Optional[list[IssueType]] = Field(alias='values')


class Issue(CustomModel):
    id: Optional[str]
    key: Optional[str]
    fields: Optional[Fields]


class Issues(CustomModel):
    issues: Optional[list[Issue]] = []


class Project(CustomModel):
    id: str
    key: str
    name: str


class Priority(CustomModel):
    id: str
    name: str


class Schema(CustomModel):
    type: Optional[str]
    items: Optional[str]
    system: Optional[str]
    custom: Optional[str]
    customId: Optional[int]


class FieldDefaultValue(CustomModel):
    id: Optional[str]
    name: Optional[str]


class FieldSchema(CustomModel):
    required: bool
    schema_: Schema = Field(alias='schema')
    name: str
    fieldId: str
    hasDefaultValue: bool
    defaultValue: Optional[Any]
    allowedValues: Optional[Any]
    autoCompleteUrl: Optional[Any]


class CreateMeta(CustomModel):
    fields: Optional[list[FieldSchema]] = Field(alias='values')


class User(CustomModel):
    name: str
    displayName: str


class CreateIssue(CustomModel):
    errors: Optional[dict]
    id: Optional[str]
    key: Optional[str]


@dataclass
class Response:
    data: bytes
    status_code: int

    def json(self):
        if self.data:
            return json.loads(self.data)
        return {}

    def __bool__(self):
        if self.status_code < 400:
            return True
        return False


class JiraAsync:
    def __init__(
            self,
            base_url: str = None,
            api_version: str = '2',
            user: str = None,
            password: str = None,
            headers: dict = None,
            logger: Optional[logging.Logger] = None
    ):
        self.auth = aiohttp.BasicAuth(login=user or environ.get("JIRA_USER"),
                                      password=password or environ.get("JIRA_PASS"))
        self.base_url = (base_url or environ.get("JIRA_URL")).strip('/ ')
        self.api_version = api_version
        self.api = self.base_url + '/rest/api/' + self.api_version
        self.headers = headers if headers else {'X-Atlassian-Token': 'no-check',
                                                'content_type': 'application/json'}
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    async def _api_call(
            self,
            method: str,
            path: str,
            json: dict = None,
            query: dict = None,
            headers: dict = None,
            data=None,
    ) -> Response:
        if query:
            path += "?" + "&".join(f"{key}={value}" for key, value in query.items())
        async with aiohttp.ClientSession(auth=self.auth) as session:
            async with session.request(
                    method=method,
                    url=self.api + path,
                    headers=headers or self.headers,
                    data=data,
                    json=json
            ) as response:
                self._logger.info(f'JIRA {"SUCCESS" if response.status < 400 else "FAIL"} '
                                  f'{response.status} - {method} {path}')
                return Response(data=await response.read(),
                                status_code=response.status)

    async def get_issue(
            self,
            issue_key: str,
            fields: str | Iterable[str | int] = '*',
            **kwargs
    ) -> Issue:
        if isinstance(fields, Iterable) and not isinstance(fields, str):
            fields = ','.join(fields)
        response = await self._api_call(
            method='get',
            path=f'/issue/{issue_key}?fields={fields}',
            **kwargs
        )
        return Issue(**response.json())

    async def upload_attachment(
            self,
            issue_key: str,
            filename: str,
            file: bytes,
            headers: dict = None,
            **kwargs
    ) -> Response:
        headers = headers or {'X-Atlassian-Token': 'no-check'}
        data = aiohttp.FormData(quote_fields=False)
        data.add_field(
            'file',
            file,
            content_type='multipart/form-data',
            filename=filename
        )
        return await self._api_call(
            method='post',
            path=f'/issue/{issue_key}/attachments',
            headers=headers,
            data=data,
            **kwargs
        )

    async def add_comment(
            self,
            issue_key: str,
            comment: str,
            **kwargs
    ) -> Response:
        return await self._api_call(
            method='post',
            path=f'/issue/{issue_key}/comment',
            json={'body': comment},
            **kwargs
        )

    async def edit_comment(
            self,
            issue_key: str,
            comment_id: str,
            comment: str,
            **kwargs
    ) -> Response:
        return await self._api_call(
            method='put',
            path=f'/issue/{issue_key}/comment/{comment_id}',
            json={'body': comment},
            **kwargs
        )

    async def delete_comment(
            self,
            issue_key: str,
            comment_id: str,
            **kwargs
    ) -> Response:
        return await self._api_call(
            method='delete',
            path=f'/issue/{issue_key}/comment/{comment_id}',
            **kwargs
        )

    async def add_watcher(
            self,
            issue_key: str,
            user_id: str,
            **kwargs
    ) -> Response:
        return await self._api_call(
            method='post',
            path=f'/issue/{issue_key}/watchers',
            data=user_id,
            **kwargs
        )

    async def get_projects(
            self,
            **kwargs
    ) -> list[Project]:
        response = await self._api_call(
            method='get',
            path='/project',
            **kwargs
        )
        return [Project(**project) for project in response.json()]

    async def search_by_jql(
            self,
            jql: str,
            start: str | int = 0,
            limit: str | int = 10,
            fields: str | Iterable[str | int] = 'summary',
            **kwargs
    ) -> Issues:
        if isinstance(fields, Iterable) and not isinstance(fields, str):
            fields = ','.join(fields)
        response = await self._api_call(
            method='get',
            path='/search',
            query={
                'start': start,
                'limit': limit,
                'fields': fields,
                'jql': jql
            },
            **kwargs
        )
        return Issues(**response.json())

    async def get_priorities(
            self,
            **kwargs
    ) -> list[Priority]:
        response = await self._api_call(
            method='get',
            path='/priority',
            **kwargs
        )
        return [Priority(**priority) for priority in response.json()]

    async def get_create_meta(
            self,
            project_key: str,
            issuetype: str,
            **kwargs
    ) -> CreateMeta:
        response = await self._api_call(
            method='get',
            path=f'/issue/createmeta/{project_key}/issuetypes/{issuetype}',
            **kwargs
        )
        return CreateMeta(**response.json())

    async def get_issuetypes(
            self,
            project_key: str,
            **kwargs
    ) -> Issuetypes:
        response = await self._api_call(
            method='get',
            path=f'/issue/createmeta/{project_key}/issuetypes',
            **kwargs
        )
        return Issuetypes(**response.json())

    async def create_issue(
            self,
            fields: dict,
            **kwargs
    ) -> CreateIssue:
        fields.update({'labels': ['slack-driven-development']})
        response = await self._api_call(
            method='post',
            path='/issue',
            json={'fields': fields},
            **kwargs
        )
        return CreateIssue(**response.json())

    async def get_issue_comments(
            self,
            issue_key: str
    ) -> list[Comment]:
        issue = await self.get_issue(
            issue_key=issue_key,
            fields='comment'
        )
        return issue.fields.comment.comments

    async def get_issue_attachments(
            self,
            issue_key: str
    ) -> list[Attachment]:
        issue = await self.get_issue(
            issue_key=issue_key,
            fields='attachment'
        )
        return issue.fields.attachment

    async def find_user(
            self,
            username: str,
            startAt: int = 0,
            maxResults: int = 10
    ) -> list[User]:
        response = await self._api_call(
            method='get',
            path='/user/search',
            query={'includeActive': True,
                   'includeInactive': False,
                   'startAt': startAt,
                   'maxResults': maxResults,
                   'username': username})
        return [User(**user) for user in response.json()]
