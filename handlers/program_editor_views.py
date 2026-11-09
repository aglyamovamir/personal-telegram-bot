"""Pure text/button descriptions, with no SQL and no Telegram requests."""


def button(label, action, *args):
    return (label, [action, *args])


def page_slice(items, page, size=8):
    page = max(0, min(page, max(0, (len(items) - 1) // size)))
    return page, list(enumerate(items))[page * size:(page + 1) * size]


def nav(rows, page, count, action, *args, size=8):
    line = []
    if page:
        line.append(button('←', action, *args, page - 1))
    if (page + 1) * size < count:
        line.append(button('→', action, *args, page + 1))
    if line:
        rows.append(line)


def exits(back_action, *args):
    return [[button('Назад', back_action, *args)], [button('Закрыть редактор', 'close')]]


def list_view(items, page, archived):
    page, visible = page_slice(items, page)
    title = 'Архив программ' if archived else 'Список программ'
    text = title + ('\nВыберите программу.' if items else '\nСоздайте программу: добавьте тренировочные дни и упражнения.')
    rows = [[button(('✓ ' if p['is_active'] else '') + p['name'], 'open', p['id'])] for _, p in visible]
    nav(rows, page, len(items), 'list', archived)
    if not archived:
        rows.append([button('Создать программу', 'new')])
    rows.append([button('Список программ' if archived else 'Архив', 'list', not archived, 0)])
    rows += exits('close')
    return text, rows


def program_view(editor, page):
    draft = editor['draft']
    days = draft['days']
    page, visible = page_slice(days, page)
    text = f"Программа: {draft['name']}" + (' ✓ активная' if draft.get('is_active') else '')
    if editor.get('program_id'):
        text += '\nИзменения сохранены.'
    text += f'\nДней: {len(days)}. Порядок не связан с неделями.'
    rows = [[button(f"{i + 1}. {d['name']} ({len(d['exercises'])} упр.)", 'day', i, 0)] for i, d in visible]
    nav(rows, page, len(days), 'program')
    if draft.get('archived_at'):
        text += '\nПрограмма в архиве.'
        rows = [[button('Восстановить', 'restore')], [button('Просмотреть программу', 'preview', 0)],
                [button('Удалить программу', 'delete_program')]]
    else:
        rows += [[button('Добавить день', 'ask', 'day_new')],
                 [button('Просмотреть программу', 'preview', 0)],
                 [button('Переименовать программу', 'ask', 'program_name')]]
        if len(days) > 1:
            rows.append([button('Изменить порядок дней', 'reorder_days', 0)])
        if editor.get('program_id'):
            if not draft.get('is_active'):
                rows.append([button('Сделать активной', 'activate')])
            rows += [[button('В архив', 'archive')], [button('Удалить программу', 'delete_program')]]
    if editor.get('rejected_change'):
        text += '\nПредыдущий ввод не был применён; его можно просмотреть.'
        rows.append([button('Посмотреть мой ввод', 'rejected_preview', 0)])
    rows += exits('leave')
    return text, rows


def day_view(editor, index, page):
    day = editor['draft']['days'][index]
    items = day['exercises']
    page, visible = page_slice(items, page)
    lines = [day['name']]
    lines += [f"{i + 1}. {e['name']}" for i, e in visible]
    if not items:
        lines.append('Добавьте первое упражнение. Пустой день не сохраняется.')
    else:
        lines.append('Изменения сохранены.')
    rows = [[button(f"{i + 1}. {e['name']}", 'exercise', index, i)] for i, e in visible]
    nav(rows, page, len(items), 'day', index)
    rows += [[button('Добавить упражнение', 'ask', 'search', index)],
             [button('Переименовать день', 'ask', 'day_name', index)]]
    if len(items) > 1:
        rows.append([button('Изменить порядок', 'reorder_exercises', index, 0)])
    rows += [[button('Удалить день', 'remove_day', index)]] + exits('program', 0)
    return '\n'.join(lines), rows


def reorder_view(editor, kind, day=None, page=0):
    items = editor['draft']['days'] if kind == 'day' else editor['draft']['days'][day]['exercises']
    page, visible = page_slice(items, page)
    rows = [[button(f"{i + 1}. {item['name']}", 'position_' + kind, *([] if day is None else [day]), i, 0)]
            for i, item in visible]
    nav(rows, page, len(items), 'reorder_days' if kind == 'day' else 'reorder_exercises', *([] if day is None else [day]))
    rows += exits('program', 0) if kind == 'day' else exits('day', day, 0)
    return 'Выберите ' + ('день' if kind == 'day' else 'упражнение') + ', которое хотите переместить.', rows


def position_view(editor, kind, source, day=None, page=0):
    items = editor['draft']['days'] if kind == 'day' else editor['draft']['days'][day]['exercises']
    page, visible = page_slice(items, page, size=24)
    args = [] if day is None else [day]
    buttons = [button(str(i + 1), 'place_' + kind, *args, source, i) for i, _ in visible]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    nav(rows, page, len(items), 'position_' + kind, *args, source, size=24)
    rows += exits('reorder_days', 0) if kind == 'day' else exits('reorder_exercises', day, 0)
    return f"{source + 1}. {items[source]['name']}\nВыберите новую позицию.", rows


def catalog_card(item):
    text = item['name']
    if item['name'] != item['name_en']:
        text += '\nАнглийское название: ' + item['name_en']
    text += '\nОсновные мышцы: ' + (', '.join(item['muscles']) or 'Не указаны')
    text += '\nОборудование: ' + item['equipment_label']
    if not item['image']:
        text += '\nИзображение отсутствует.'
    return text


def format_plan(items):
    if not items:
        return 'нет'
    lines = []
    for item in items:
        weight = '-' if item['weight'] is None else f"{item['weight']:g}"
        reps = '-' if item['reps'] is None else str(item['reps'])
        lines.append(f'{weight} × {reps}')
    return ', '.join(lines)


def day_summary(day):
    lines = [day['name']]
    for i, exercise in enumerate(day['exercises'], 1):
        lines.append(f"{i}. {exercise['name']}")
        if exercise.get('working_sets') is not None:
            lines.append(f"Необязательный план рабочих ({exercise['working_sets']}): {format_plan(exercise['working_plan'])}")
        if exercise.get('warmup'):
            lines.append(f"План разминки: {format_plan(exercise['warmup'])}")
    return '\n'.join(lines)


def exercise_view(editor, day_index, index):
    exercise = editor['draft']['days'][day_index]['exercises'][index]
    text = f"{index + 1}. {exercise['name']}\nВсе выполненные подходы запишите во время тренировки. Изменения программы сохраняются автоматически."
    rows = [[button('Моё название', 'ask', 'exercise_name', day_index, index)],
            [button('Удалить упражнение', 'remove_exercise', day_index, index)],
            [button('Необязательный план', 'optional_plan', day_index, index)]]
    rows += exits('day', day_index, index // 8)
    return text, rows


def optional_plan_view(editor, day_index, index):
    exercise = editor['draft']['days'][day_index]['exercises'][index]
    text = f"Необязательный план: {exercise['name']}\nДля создания программы и записи тренировки заполнять его не нужно. Старые настройки сохраняются."
    if exercise.get('working_sets') is not None:
        text += f"\nРабочие ({exercise['working_sets']}): {format_plan(exercise['working_plan'])}"
    else:
        text += '\nЧисло рабочих подходов не задано.'
    text += f"\nРазминка: {format_plan(exercise['warmup'])}"
    rows = [[button('Число рабочих подходов', 'ask', 'count', day_index, index)],
            [button('Вес и повторения рабочих', 'ask', 'working', day_index, index)],
            [button('Разминочные подходы', 'ask', 'warmup', day_index, index)]]
    rows += exits('exercise', day_index, index)
    return text, rows
