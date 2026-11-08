"""Search the single shared catalog with Unicode case folding and literal text."""
from database import db


def search_exercises(query, page=0, page_size=8):
    query = query.strip()
    if not 2 <= len(query) <= 100:
        raise ValueError('Введите от 2 до 100 символов названия')
    if type(page) is not int or page < 0 or not 1 <= page_size <= 20:
        raise ValueError('Неверная страница поиска')
    with db._connection() as c:
        c.create_function('casefold', 1, lambda text: (text or '').casefold())
        where = 'instr(casefold(name_ru),?)>0 OR instr(casefold(name_en),?)>0'
        params = (query.casefold(), query.casefold())
        count = c.execute(f'SELECT COUNT(*) FROM exercises WHERE {where}', params).fetchone()[0]
        page = min(page, max(0, (count - 1) // page_size))
        rows = c.execute(f'SELECT external_id,name_ru,name_en FROM exercises WHERE {where} ORDER BY id LIMIT ? OFFSET ?',
                         (*params, page_size, page * page_size)).fetchall()
        return dict(page=page, total=count, items=[dict(exercise_id=e, name=r or n, name_en=n) for e, r, n in rows])
