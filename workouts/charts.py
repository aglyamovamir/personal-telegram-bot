"""Standalone PNG charts in memory; no GUI, database writes or network."""
from io import BytesIO
from datetime import timedelta
from math import isfinite
from textwrap import fill
from threading import Lock

_lock = Lock()  # matplotlib is not thread safe; rendering runs off the event loop.


def render_chart(records, title, *, exercise_label=None, selected_id=None):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.dates import AutoDateLocator, DateFormatter

    rows = [r for r in records if r['date'] is not None]
    rows.sort(key=lambda r: (r['date'].timestamp(), r['id']))
    if not rows:
        return None
    metrics = [('maximum', exercise_label, 'кг'), ('volume', 'Объём рабочих подходов', 'кг·повт.')] if exercise_label else [
        ('volume', 'Объём рабочих подходов', 'кг·повт.')]
    with _lock:
        figure = Figure(figsize=(10, 4.4 * len(metrics)), dpi=130, layout='constrained')
        FigureCanvasAgg(figure)
        figure.suptitle(fill(title, 65), fontsize=14)
        for index, (field, label, unit) in enumerate(metrics, 1):
            ax = figure.add_subplot(len(metrics), 1, index)
            points = []
            for record in rows:
                try:
                    value = float(record['metrics'][field])
                except (ValueError, TypeError, OverflowError):
                    continue
                if isfinite(value):
                    points.append((record['date'], value, record['id']))
            if points:
                ax.plot([p[0] for p in points], [p[1] for p in points], color='#2962aa',
                        marker='o' if len(points) <= 70 else None, markersize=4, linewidth=1.6)
                chosen = next((p for p in points if p[2] == selected_id), points[-1])
                ax.scatter([chosen[0]], [chosen[1]], color='#ca6d15', marker='D', zorder=3)
                ax.annotate(f'{chosen[1]:g}', xy=chosen[:2], xytext=(0, 9), textcoords='offset points', ha='center')
                ax.set_ylim(bottom=0, top=max(1, max(p[1] for p in points) * 1.18))
            else:
                ax.text(.5, .5, 'Нет подходящих числовых данных', transform=ax.transAxes, ha='center')
            ax.set_title(label, fontsize=11)
            ax.set_ylabel(unit)
            ax.set_xlabel('Дата тренировки')
            ax.grid(axis='y', alpha=.22)
            locator = AutoDateLocator(minticks=3, maxticks=7)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(DateFormatter('%d.%m.%y', tz=rows[0]['date'].tzinfo))
            if points and len({p[0].timestamp() for p in points}) == 1:
                date = points[0][0]
                ax.set_xlim(date - timedelta(days=1), date + timedelta(days=1))
                ax.set_xticks([date])
            ax.tick_params(axis='x', labelsize=9, rotation=20)
        output = BytesIO()
        figure.savefig(output, format='png')
        figure.clear()
        return output.getvalue()
