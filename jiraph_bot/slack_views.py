def no_access_view(app_name: str) -> dict:
    return {'blocks': [{'block_id': 'add_app_warning',
                              'text': {'text': f'Please, add {app_name} app into channel '
                                               'integrations before using shortcuts',
                                       'type': 'mrkdwn'},
                              'type': 'section'}],
                  'title': {'emoji': True,
                            'text': f'{app_name}',
                            'type': 'plain_text'},
                  'type': 'modal'}

def view_value_path(field: str) -> tuple[str, ...]:
    return ('view', 'state', 'values', field, field, 'selected_option', 'value')


def choose_project_view(private_metadata: str) -> dict:
    return {'blocks': [{'block_id': 'choose_project',
                        'dispatch_action': True,
                        'element': {'action_id': 'choose_project',
                                    'min_query_length': 0,
                                    'placeholder': {'emoji': True,
                                                    'text': 'Jira projects',
                                                    'type': 'plain_text'},
                                    'type': 'external_select'},
                        'label': {'emoji': True,
                                  'text': 'Jira Project',
                                  'type': 'plain_text'},
                        'type': 'input'}],
            'callback_id': 'issue-from-thread',
            'private_metadata': private_metadata,
            'title': {'emoji': True,
                      'text': 'Issue',
                      'type': 'plain_text'},
            'type': 'modal'}


def empty_view(private_metadata: str) -> dict:
    return {'blocks': [],
            'callback_id': 'issue-from-thread',
            'private_metadata': private_metadata,
            'title': {'emoji': True,
                      'text': 'Issue',
                      'type': 'plain_text'},
            'type': 'modal'}


def issuetypes_block(issuetypes: list) -> dict:
    return {'block_id': 'issue_type',
            'dispatch_action': True,
            'element': {'action_id': 'issue_type',
                        'options': issuetypes,
                        'placeholder':
                            {'emoji': True,
                             'text': 'Issue type',
                             'type': 'plain_text'},
                        'type': 'static_select'},
            'label': {'emoji': True,
                      'text': 'Issue type',
                      'type': 'plain_text'},
            'type': 'input'}


def choose_issue_view(private_metadata: str) -> dict:
    return {'blocks': [{'block_id': 'find_issue',
                        'element': {'action_id': 'find_issue',
                                    'min_query_length': 2,
                                    'placeholder':
                                        {'emoji': True,
                                         'text': 'Jira issues',
                                         'type': 'plain_text'},
                                    'type': 'external_select'},
                        'label': {'emoji': True,
                                  'text': 'Select issue',
                                  'type': 'plain_text'},
                        'type': 'input'}],
            'callback_id': 'attach-to-issue',
            'private_metadata': private_metadata,
            'submit': {'emoji': True,
                       'text': 'Send',
                       'type': 'plain_text'},
            'title': {'emoji': True,
                      'text': 'Send thread to issue',
                      'type': 'plain_text'},
            'type': 'modal'}


def summary(
        project_key: str,
        issuetype: str,
        message: str
) -> dict:
    return {'block_id': 'summary',
            'element': {'action_id': f'field.{project_key}.{issuetype}.summary',
                        'max_length': 255,
                        'multiline': False,
                        'initial_value': message,
                        'placeholder': {'emoji': True,
                                        'text': 'Summary',
                                        'type': 'plain_text'},
                        'type': 'plain_text_input'},
            'label': {'emoji': True,
                      'text': 'Summary',
                      'type': 'plain_text'},
            'optional': False,
            'type': 'input'}


def descrtiption(
        project_key: str,
        issuetype: str,
        message: str
) -> dict:
    return {'block_id': 'description',
            'element': {
                'action_id': f'field.{project_key}.{issuetype}.description',
                'multiline': True,
                'initial_value': message,
                'placeholder': {'emoji': True,
                                'text': 'Description',
                                'type': 'plain_text'},
                'type': 'plain_text_input'},
            'label': {'emoji': True,
                      'text': 'Description',
                      'type': 'plain_text'},
            'optional': True,
            'type': 'input'}


def make_input_block(
        field, project, issuetype):
    schema = field.schema_
    match schema.type:
        case 'user' | 'option' | 'priority':
            block = {'block_id': field.fieldId,
                     'element': {'action_id': f'field.{project}.{issuetype}.{field.fieldId}',
                                 'min_query_length': 0,
                                 'placeholder': {'emoji': True,
                                                 'text': field.name,
                                                 'type': 'plain_text'},
                                 'type': 'external_select'},
                     'label': {'emoji': True,
                               'text': field.name,
                               'type': 'plain_text'},
                     'optional': not field.required,
                     'type': 'input'}
            if field.hasDefaultValue and isinstance(field.defaultValue, dict) and field.defaultValue.get('id'):
                block['element']['initial_option'] = {'text': {"type": "plain_text",
                                                               "text": field.defaultValue.get('name')},
                                                      'value': f'{{"id": "{field.defaultValue["id"]}"}}'}
            return block
        case 'date':
            return {'block_id': field.fieldId,
                    'element': {'action_id': f'field.{project}.{issuetype}.{field.fieldId}',
                                'placeholder': {'emoji': True,
                                                'text': field.name,
                                                'type': 'plain_text'},
                                'type': 'datepicker'},
                    'label': {'emoji': True,
                              'text': field.name,
                              'type': 'plain_text'},
                    'optional': not field.required,
                    'type': 'input'}
        case 'string' | 'number':
            return {'block_id': field.fieldId,
                    'element': {'action_id': f'field.{project}.{issuetype}.{field.fieldId}',
                                'multiline': False,
                                'placeholder': {'emoji': True,
                                                'text': field.name,
                                                'type': 'plain_text'},
                                'type': 'plain_text_input'},
                    'label': {'emoji': True,
                              'text': field.name,
                              'type': 'plain_text'},
                    'optional': not field.required,
                    'type': 'input'}
        case 'array':
            return {'block_id': field.fieldId,
                    'element': {'action_id': f'field.{project}.{issuetype}.{field.fieldId}',
                                'min_query_length': 0,
                                'placeholder': {'emoji': True,
                                                'text': field.name,
                                                'type': 'plain_text'},
                                'type': 'multi_external_select'},
                    'label': {'emoji': True,
                              'text': field.name,
                              'type': 'plain_text'},
                    'optional': not field.required,
                    'type': 'input'}
