"""Read-only multilingual search over the existing shared catalog."""
import re
import sqlite3
import unicodedata
import ipaddress
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from database import db
from database.catalog_localization import ALIASES, RUSSIAN_NAMES


# These equivalents affect search only. Meaningful qualifiers stay mandatory:
# a query for weighted dips must never be satisfied by ordinary/assisted dips.
SEARCH_PHRASES = {
    'с дополнительным весом': 'weighted',
    'с дополнительным отягощением': 'weighted',
    'с отягощением': 'weighted',
    'с весом': 'weighted',
    'с собственным весом': 'bodyweight',
    'собственный вес': 'bodyweight',
    'брусьях': 'брусья',
    'гантелями': 'dumbbell', 'гантелей': 'dumbbell',
    'гантелью': 'dumbbell', 'гантель': 'dumbbell', 'гантели': 'dumbbell',
    'штангой': 'barbell', 'штанги': 'barbell', 'штанга': 'barbell',
    'гирями': 'kettlebell', 'гирей': 'kettlebell', 'гири': 'kettlebell',
    'гирю': 'kettlebell', 'гиря': 'kettlebell',
    'бицепса': 'бицепс', 'трицепса': 'трицепс',
    'разводка': 'разведение', 'разведения': 'разведение',
    'сгибания': 'сгибание', 'подъемы': 'подъем',
    'резинкой': 'резиновой петлей',
    'dumbbells': 'dumbbell', 'kettlebells': 'kettlebell',
    'pull ups': 'pull up', 'push ups': 'push up',
}
STOP_WORDS = frozenset(('с', 'со', 's', 'на', 'в', 'во', 'от', 'для', 'и',
                        'the', 'with', 'on', 'a', 'an', 'to', 'of'))
LABELS = {
    'barbell': 'Штанга', 'dumbbell': 'Гантели', 'cable': 'Блок',
    'dip_machine': 'Тренажёр для брусьев', 'bodyweight': 'Собственный вес',
    'chest': 'Грудь', 'shoulders': 'Плечи', 'upper_arms': 'Руки',
    'biceps': 'Бицепс', 'triceps': 'Трицепс', 'pectoralis_major': 'Большая грудная',
    'deltoids': 'Дельтовидные', 'quadriceps': 'Квадрицепс', 'hamstrings': 'Задняя поверхность бедра',
    'glutes': 'Ягодичные', 'lats': 'Широчайшие', 'back': 'Спина', 'abs': 'Пресс',
    'rectus_abdominis': 'Прямая мышца живота', 'transverse_abdominis': 'Поперечная мышца живота',
    'anterior_deltoid': 'Передняя дельта', 'lateral_deltoid': 'Средняя дельта',
    'posterior_deltoid': 'Задняя дельта', 'latissimus_dorsi': 'Широчайшие',
    'obliques': 'Косые мышцы живота', 'serratus_anterior': 'Передняя зубчатая',
    'gluteus_maximus': 'Большая ягодичная', 'gluteus_medius': 'Средняя ягодичная',
    'biceps_brachii': 'Бицепс', 'triceps_brachii': 'Трицепс',
    'forearm_flexors': 'Сгибатели предплечья', 'forearm_extensors': 'Разгибатели предплечья',
    'forearms': 'Предплечья', 'rhomboids': 'Ромбовидные', 'trapezius': 'Трапеции',
    'erector_spinae': 'Разгибатели спины', 'gastrocnemius': 'Икроножная',
    'hip_flexors': 'Сгибатели бедра', 'adductors': 'Приводящие бедра',
    'abductors': 'Отводящие бедра', 'soleus': 'Камбаловидная',
    'brachialis': 'Плечевая', 'brachioradialis': 'Плечелучевая',
    'quadratus_lumborum': 'Квадратная мышца поясницы', 'supraspinatus': 'Надостная',
    'ab_wheel': 'Ролик для пресса', 'air_bike': 'Аэробайк', 'pull_up_bar': 'Турник',
    'assisted_pullup_machine': 'Гравитрон', 'stability_ball': 'Фитбол',
    'resistance_band': 'Эспандер', 'loop_band': 'Резиновая петля',
    'battle_rope': 'Канаты', 'lat_pulldown_machine': 'Верхний блок',
    'flat_bench': 'Скамья', 'ez_bar': 'EZ-гриф', 'plyo_box': 'Тумба',
    'chest_press_machine': 'Тренажёр для жима от груди', 'kettlebell': 'Гиря',
    'smith_machine': 'Тренажёр Смита', 'leg_press': 'Тренажёр для жима ногами',
    'dip_station': 'Брусья', 'elliptical': 'Эллипсоид', 'hack_squat': 'Гакк-тренажёр',
    'trap_bar': 'Трэп-гриф', 'hip_abduction_machine': 'Тренажёр для разведения ног',
    'hip_adduction_machine': 'Тренажёр для сведения ног', 'treadmill': 'Беговая дорожка',
    'jump_rope': 'Скакалка', 'leg_curl': 'Тренажёр для сгибания ног',
    'leg_extension': 'Тренажёр для разгибания ног', 'back_extension_machine': 'Гиперэкстензия',
    'bicep_curl_machine': 'Тренажёр для бицепса', 'standing_calf_raise_machine': 'Тренажёр для икр стоя',
    'chest_fly_machine': 'Тренажёр для сведения рук', 'preacher_curl_machine': 'Скамья Скотта',
    'ab_crunch_machine': 'Тренажёр для пресса', 'shoulder_press_machine': 'Тренажёр для жима на плечи',
    'tricep_extension_machine': 'Тренажёр для трицепса', 'slam_ball': 'Медбол',
    'glute_ham_developer': 'GHD-тренажёр', 'pec_deck': 'Бабочка',
    'donkey_calf_raise_machine': 'Тренажёр для икр в наклоне', 'hip_thrust_machine': 'Тренажёр для ягодичного моста',
    'plate_loaded_lateral_raise_machine': 'Тренажёр для разведения рук', 'shrug_machine': 'Тренажёр для шрагов',
    'plates': 'Блины', 'rings': 'Кольца', 'climbing_rope': 'Канат', 'rower': 'Гребной тренажёр',
    'seated_calf_raise_machine': 'Тренажёр для икр сидя', 'sled': 'Сани',
    'stair_climber': 'Степпер', 'stationary_bike': 'Велотренажёр',
    'suspension_trainer': 'Петли TRX', 'wrist_roller': 'Кистевой тренажёр',
}


def normalize(text):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', text or '').casefold().replace('ё', 'е')))


def russian_name(value):
    """Accept complete Cyrillic names, never corrupted or mixed-word output."""
    value = (value or '').strip()
    return bool(value and re.search('[а-яёА-ЯЁ]', value)
                and not re.search('[a-zA-Z\ufffd]', value))


def names(external, ru, en):
    display = ru.strip() if russian_name(ru) else RUSSIAN_NAMES.get(external, en)
    # Keep the complete dictionary name searchable even when an administrator
    # supplied a different valid Russian name in the shared catalog.
    candidates = [display, en, RUSSIAN_NAMES.get(external, ''), *ALIASES.get(external, ())]
    return display, candidates


@lru_cache(maxsize=8192)
def search_tokens(value):
    value = normalize(value)
    for phrase, replacement in SEARCH_PHRASES.items():
        value = re.sub(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', replacement, value)
    return tuple(word for word in value.split() if word not in STOP_WORDS)


def score(query, candidates, exact_aliases=()):
    query = normalize(query)
    tokens = search_tokens(query)
    if not tokens:
        return None
    best = None
    for index, candidate in enumerate(candidates):
        text = normalize(candidate)
        if not text:
            continue
        words = search_tokens(text)
        if query == text:
            explicit = query in {normalize(alias) for alias in exact_aliases}
            rank = (0, 0) if index < 2 else (1, 0 if explicit else len(normalize(candidates[0])))
        elif text.startswith(query):
            rank = (2, len(text) - len(query))
        elif all(any(word.startswith(token) for word in words) for token in tokens):
            rank = (3, len(words) - len(tokens), len(text))
        elif query in text:
            rank = (4, len(text))
        else:
            similarities = [max((SequenceMatcher(None, token, word).ratio() for word in words), default=0) for token in tokens]
            similarity = sum(similarities) / len(tokens)
            if len(query) < 4 or min(similarities) < .65 or similarity < .78:
                continue
            rank = (5, -similarity, len(words))
        if best is None or rank < best:
            best = rank
    return best


def search_exercises(query, page=0, page_size=8):
    query = normalize(query)
    if not 2 <= len(query) <= 100:
        raise ValueError('Введите от 2 до 100 символов названия')
    if type(page) is not int or page < 0 or type(page_size) is not int or not 1 <= page_size <= 20:
        raise ValueError('Неверная страница поиска')
    with db._connection() as c:
        rows = c.execute('SELECT external_id,name_ru,name_en FROM exercises ORDER BY id').fetchall()
    matches = []
    for external, ru, en in rows:
        display, candidates = names(external, ru, en)
        rank = score(query, candidates, ALIASES.get(external, ()))
        if rank is not None:
            matches.append((rank, normalize(display), dict(exercise_id=external, name=display, name_en=en)))
    matches.sort(key=lambda row: (row[0], row[1], row[2]['exercise_id']))
    page = min(page, max(0, (len(matches) - 1) // page_size))
    return dict(page=page, total=len(matches), items=[r[2] for r in matches[page * page_size:(page + 1) * page_size]])


def image_source(value):
    """Validate references; local files must stay inside the existing image root."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme in ('http', 'https'):
        if (not parsed.hostname or parsed.username or parsed.password
                or parsed.hostname.lower() == 'localhost' or any(c.isspace() for c in value)
                or port == 0):
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                return None
        except ValueError:
            if '.' not in parsed.hostname:
                return None
        return value
    if parsed.scheme or parsed.netloc:
        return None
    root = (Path(__file__).resolve().parents[1] / 'data' / 'repdb').resolve()
    try:
        path = (root / value).resolve()
    except (OSError, ValueError):
        return None
    if root not in path.parents or path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
        return None
    return path if path.is_file() else None


def get_exercise(external):
    with db._connection() as c:
        c.row_factory = sqlite3.Row
        row = c.execute('SELECT * FROM exercises WHERE external_id=?', (external,)).fetchone()
        if row is None:
            raise ValueError('Упражнение больше недоступно. Повторите поиск.')
        data = dict(row)
        muscles = c.execute("SELECT muscle FROM exercise_muscles WHERE exercise_id=? AND muscle_type='primary' ORDER BY muscle", (data['id'],)).fetchall()
        paths = c.execute('SELECT view,path FROM exercise_images WHERE exercise_id=? ORDER BY id', (data['id'],)).fetchall()
    data['name'], _ = names(external, data.get('name_ru'), data['name_en'])
    data['muscles'] = [LABELS.get(r[0], r[0].replace('_', ' ')) for r in muscles[:5]]
    data['equipment_label'] = LABELS.get(data.get('equipment'), data.get('equipment') or 'Не указано')
    data['has_russian_name'] = russian_name(data['name'])
    data['images'] = exercise_images(paths, data.get('image_url'))
    data['image'] = data['images'][0]['source'] if data['images'] else None
    return data


def exercise_images(paths, image_url=None):
    """Return at most two distinct existing references, with truthful phase labels.

    Related ``start``/``peak`` images take precedence over the legacy main image;
    ``peak`` is not an end position. Unknown views receive neutral captions.
    Transport failures are handled by the UI so the text card remains usable.
    """
    views = {'start': (0, 'Исходное положение'),
             'peak': (1, 'Пиковая фаза'),
             'end': (2, 'Конечное положение'),
             'main': (3, 'Изображение упражнения')}
    candidates = sorted(paths, key=lambda row: views.get(row[0], (4, ''))[0])
    candidates.append(('main', image_url))
    images, seen = [], set()
    for view, value in candidates:
        source = image_source(value)
        if source is None or str(source) in seen:
            continue
        seen.add(str(source))
        images.append(dict(source=source, view=view,
                           caption=views.get(view, (4, 'Изображение упражнения'))[1]))
        if len(images) == 2:
            break
    return images
