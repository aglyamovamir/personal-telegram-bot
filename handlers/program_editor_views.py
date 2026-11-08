"""Pure text/button descriptions, with no SQL and no Telegram requests."""


def button(label, action, *args):
    return (label, [action, *args])


def page_slice(items, page, size=8):
    page = max(0, min(page, max(0, (len(items) - 1) // size)))
    return page, list(enumerate(items))[page * size:(page + 1) * size]


def nav(rows, page, count, action, *args):
    line = []
    if page:
        line.append(button('←', action, *args, page - 1))
    if (page + 1) * 8 < count:
        line.append(button('→', action, *args, page + 1))
    if line:
        rows.append(line)


def list_view(items, page, archived):
    page, visible = page_slice(items, page)
    title = 'Архив программ' if archived else 'Мои программы'
    text = title + ('\nВыберите программу.' if items else '\nПрограмм пока нет. Создайте свою первую программу.')
    rows = [[button(('✓ ' if p['is_active'] else '') + p['name'], 'open', p['id'])] for _, p in visible]
    nav(rows, page, len(items), 'list', archived)
    if not archived:
        rows.append([button('＋ Создать программу', 'new')])
    rows.append([button('Мои программы' if archived else 'Архив', 'list', not archived, 0)])
    rows.append([button('Отмена', 'close')])
    return text, rows


def program_view(editor, page):
    draft = editor['draft']
    days = draft['days']
    page, visible = page_slice(days, page)
    text = f"Программа: {draft['name']}" + (' ✓ активная' if draft.get('is_active') else '')
    text += '\nЕсть несохранённые изменения.' if editor['dirty'] else '\nИзменения сохранены.'
    text += f'\nДней: {len(days)}. Порядок не связан с неделями.'
    rows = [[button(f"{i + 1}. {d['name']} ({len(d['exercises'])} упр.)", 'day', i, 0)] for i, d in visible]
    nav(rows, page, len(days), 'program')
    if draft.get('archived_at'):
        text += '\nПрограмма в архиве.'
        rows = [[button('Восстановить', 'restore')], [button('Назад', 'leave')]]
    else:
        rows += [[button('＋ Добавить день', 'ask', 'day_new')],
                 [button('Просмотр состава', 'preview')],
                 [button('Переименовать', 'ask', 'program_name')],
                 [button('Сохранить', 'save'), button('Сделать активной', 'activate')]]
        if editor['program_id']:
            rows.append([button('В архив', 'archive')])
        rows.append([button('Назад', 'leave'), button('Отмена', 'leave')])
    return text, rows


def day_view(editor, index, page):
    day = editor['draft']['days'][index]
    items = day['exercises']
    page, visible = page_slice(items, page)
    text = f"День {index + 1}: {day['name']}\nУпражнения по порядку:"
    if not items:
        text += '\nПока пусто. Добавьте упражнение из каталога.'
    rows = [[button(f"{i + 1}. {e['name']}", 'exercise', index, i)] for i, e in visible]
    nav(rows, page, len(items), 'day', index)
    rows += [[button('＋ Найти упражнение', 'ask', 'search', index)],
             [button('Переименовать день', 'ask', 'day_name', index)],
             [button('День ↑', 'move_day', index, -1), button('День ↓', 'move_day', index, 1)],
             [button('Удалить день', 'remove_day', index)],
             [button('Сохранить программу', 'save')],
             [button('Назад', 'program', 0), button('Отмена', 'leave')]]
    return text, rows


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
    lines = [f"День: {day['name']}"]
    for i, exercise in enumerate(day['exercises'], 1):
        lines.append(f"{i}. {exercise['name']}")
        if exercise.get('working_sets') is not None:
            lines.append(f"Необязательный план рабочих ({exercise['working_sets']}): {format_plan(exercise['working_plan'])}")
        if exercise.get('warmup'):
            lines.append(f"План разминки: {format_plan(exercise['warmup'])}")
    return '\n'.join(lines)


def exercise_view(editor, day_index, index):
    exercise = editor['draft']['days'][day_index]['exercises'][index]
    text = f"{index + 1}. {exercise['name']}\nПрограмма задаёт порядок упражнений. Все выполненные подходы запишите во время тренировки."
    rows = [[button('Моё название', 'ask', 'exercise_name', day_index, index)],
            [button('Упражнение ↑', 'move_exercise', day_index, index, -1), button('Упражнение ↓', 'move_exercise', day_index, index, 1)],
            [button('Удалить упражнение', 'remove_exercise', day_index, index)],
            [button('Необязательный план', 'optional_plan', day_index, index)],
            [button('＋ Добавить ещё упражнение', 'ask', 'search', day_index)],
            [button('Назад', 'day', day_index, 0), button('Отмена', 'leave')]]
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
            [button('Разминочные подходы', 'ask', 'warmup', day_index, index)],
            [button('Назад', 'exercise', day_index, index), button('Отмена', 'exercise', day_index, index)]]
    return text, rows
