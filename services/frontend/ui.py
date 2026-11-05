"""Presentation layer: every visual component of the portal. Data comes from common.py."""
import re
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

CSS = Path(__file__).with_name('theme.css')
CRITICAL = '#e5484d'  # reads on both themes; status colour, never used for a series
INK = {'light': '#666666', 'dark': '#a1a1a1'}


def inject_theme():
    css = CSS.read_text()
    dark_selector = '.stApp' if color_scheme() == 'dark' else '.scheme-never'
    st.html(f'<style>{css.replace(".stApp.scheme-dark", dark_selector)}</style>')


def inline(text):
    """Escape, then apply the small subset of Markdown the copy uses."""
    out = escape(str(text))
    out = re.sub(r'`([^`]+)`', r'<code>\1</code>', out)
    out = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', out)
    return re.sub(r'\*([^*]+)\*', r'<em>\1</em>', out)


def color_scheme():
    try:
        return st.context.theme.type or 'light'
    except AttributeError:
        return 'light'


# ---- page furniture -----------------------------------------------------------------------------
def page_header(eyebrow, title, lede=None):
    if eyebrow:
        st.html(f'<div class="eyebrow">{escape(eyebrow)}</div>')
    st.title(title, anchor=False)
    if lede:
        st.html(f'<p class="lede">{inline(lede)}</p>')


def watch(text):
    st.html(f'<div class="watch"><b>Watch for</b><span>{inline(text)}</span></div>')


def heading(title, hint=None, step=None):
    label = f'<span>{escape(str(step))}</span>' if step else ''
    note = f'<p class="hint">{inline(hint)}</p>' if hint else ''
    st.html(f'<div class="heading"><div>{label}<h3>{escape(title)}</h3></div>{note}</div>')


def chart_title(title, unit=''):
    st.html(f'<div class="chart-title"><b>{escape(title)}</b><span>{escape(unit)}</span></div>')


def hint(text):
    st.html(f'<p class="hint">{inline(text)}</p>')


def waiting(text):
    st.html(f'<p class="wait">{inline(text)}</p>')


def split(left_title, left, right_title, right):
    """Two prose blocks in one frame. `left` / `right`: a string, or a list rendered as bullets."""
    def body(content):
        if isinstance(content, str):
            return f'<p>{inline(content)}</p>'
        return '<ul>' + ''.join(f'<li>{inline(item)}</li>' for item in content) + '</ul>'
    st.html(f'<div class="split"><div><h4>{escape(left_title)}</h4>{body(left)}</div>'
            f'<div><h4>{escape(right_title)}</h4>{body(right)}</div></div>')


# ---- stat tiles -----------------------------------------------------------------------------------
def stat(label, value, unit='', hint=None, tone=None):
    """One tile. `tone`: good | warn | crit, or None for neutral ink."""
    return dict(label=label, value=value, unit=unit, hint=hint, tone=tone)


def stats(items):
    cells = []
    for item in items:
        unit = f"<small>{escape(item['unit'])}</small>" if item['unit'] else ''
        note = f"<p>{escape(item['hint'])}</p>" if item['hint'] else ''
        cells.append(f"<div class=\"stat {item['tone'] or ''}\"><div class=\"k\">{escape(item['label'])}</div>"
                     f"<div class=\"v\">{escape(str(item['value']))}{unit}</div>{note}</div>")
    st.html(f'<div class="stats">{"".join(cells)}</div>')


# ---- alerts ---------------------------------------------------------------------------------------
def alert_states(names, states):
    rows = []
    for name in names:
        state = states.get(name, 'inactive')
        dot = {'firing': 'dot crit live', 'pending': 'dot warn', 'inactive': 'dot'}[state]
        rows.append(f'<div class="alert {state}"><i class="{dot}"></i><code>{escape(name)}</code><em>{state}</em></div>')
    st.html(f'<div class="alerts">{"".join(rows)}</div>')


def inbox(items, limit=6):
    if not items:
        waiting('No notification received yet.')
        return
    rows = []
    for item in items[:limit]:
        firing = item['status'] == 'firing'
        dot = 'dot crit' if firing else 'dot good'
        rows.append(f'<div><time>{escape(item["received_at"][11:19])}</time><div>'
                    f'<b><i class="{dot}"></i>{escape(item["alertname"])} <em>{escape(item["status"])}</em></b>'
                    f'<p>{inline(item["summary"])}. {inline(item["action"])}</p></div></div>')
    st.html(f'<div class="feed">{"".join(rows)}</div>')


# ---- tables ---------------------------------------------------------------------------------------
def table(frame, mono=(), strong=(), numeric=(), render=None, highlight=None):
    """Plain HTML table. `render` maps a column to a function returning trusted HTML for its cell."""
    render = render or {}
    head = ''.join(f'<th>{escape(str(col).replace("_", " "))}</th>' for col in frame.columns)
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for col in frame.columns:
            value = row[col]
            classes = ' '.join(k for k, group in (('mono', mono), ('strong', strong), ('num', numeric)) if col in group)
            if col in render:
                text = render[col](value)
            else:
                text = '' if pd.isna(value) else inline(value)
            cells.append(f'<td class="{classes}">{text}</td>')
        marked = ' class="mine"' if highlight and highlight(row) else ''
        rows.append(f'<tr{marked}>{"".join(cells)}</tr>')
    st.html(f'<div class="tbl-wrap"><table class="tbl"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def short_id(value):
    if value is None or pd.isna(value):
        return ''
    return f'<span title="{escape(str(value))}">{escape(str(value)[:8])}</span>'


def logs(frame, highlight=None):
    if frame.empty:
        waiting('No log line yet.')
        return
    view = frame.rename(columns={'duration_ms': 'ms', 'model_version': 'model', 'request_id': 'request', 'trace_id': 'trace'})
    table(view, mono=('time', 'level', 'ms', 'model', 'request', 'trace'), strong=('message',), numeric=('ms',),
          render={'level': lambda v: f'<span class="lvl {escape(str(v))}">{escape(str(v))}</span>',
                  'request': short_id, 'trace': short_id,
                  'ms': lambda v: '' if pd.isna(v) else f'{v:,.0f}'},
          highlight=highlight)


def traces(frame, slow_ms=1000):
    if frame.empty:
        waiting('No trace yet.')
        return
    longest = max(frame['duration_ms'].max(), 1)

    def bar(value):
        width = max(value / longest * 100, 1)
        tone = ' class="crit"' if value >= slow_ms else ''
        return f'<div class="bar"><i{tone} style="width:{width:.0f}%"></i><span>{value:,.0f} ms</span></div>'
    table(frame[['started', 'trace_id', 'duration_ms']].rename(columns={'trace_id': 'trace', 'duration_ms': 'duration'}),
          mono=('started', 'trace'), render={'trace': short_id, 'duration': bar})


# ---- charts (colours come from the Streamlit theme, so they follow light/dark) --------------------------
def _configure(chart):
    return (chart.configure_view(strokeWidth=0)
            .configure_axis(domain=False, ticks=False, labelFont='Geist Mono', labelFontSize=11, labelPadding=6,
                            grid=True, gridDash=[2, 3], gridOpacity=0.5)
            .configure_axisX(grid=False, labelFlush=True)
            .configure_legend(labelFont='Geist', labelFontSize=12, symbolType='stroke', symbolStrokeWidth=2))


def chart(frame, title, unit='', threshold=None, threshold_label='alert', height=210):
    """Time series with an optional threshold rule: the audience sees the metric cross the alert line."""
    with st.container(border=True):
        chart_title(title, unit)
        if frame.empty:
            waiting('Waiting for data')
            return
        series = frame['series'].nunique()
        top = max(frame['value'].max(), threshold or 0) * 1.25 or 1  # room for the threshold label
        lines = alt.Chart(frame).mark_line(strokeWidth=2, interpolate='monotone').encode(
            x=alt.X('time:T', title=None, axis=alt.Axis(format='%H:%M', tickCount=5)),
            y=alt.Y('value:Q', title=None, scale=alt.Scale(domain=[0, top], nice=False), axis=alt.Axis(tickCount=4)),
            color=alt.Color('series:N', title=None, legend=alt.Legend(orient='top', direction='horizontal') if series > 1 else None),
            tooltip=[alt.Tooltip('series:N', title='series'), alt.Tooltip('value:Q', title=unit or 'value', format=',.3~f'),
                     alt.Tooltip('time:T', title='time', format='%H:%M:%S')])
        layers = [lines]
        if threshold is not None:
            mark = pd.DataFrame({'y': [threshold], 'label': [f'{threshold_label} {threshold:g}']})
            layers.append(alt.Chart(mark).mark_rule(color=CRITICAL, strokeDash=[4, 3], strokeWidth=1.5).encode(y='y:Q'))
            layers.append(alt.Chart(mark).mark_text(align='left', baseline='bottom', dx=4, dy=-3, font='Geist Mono', fontSize=11,
                                                    color=INK[color_scheme()]).encode(y='y:Q', x=alt.value(0), text='label:N'))
        st.altair_chart(_configure(alt.layer(*layers).properties(height=height)), width='stretch')


def bar_chart(frame, category, value, threshold, title, unit):
    with st.container(border=True):
        chart_title(title, unit)
        bars = alt.Chart(frame).mark_bar(cornerRadiusEnd=3, height=14).encode(
            y=alt.Y(f'{category}:N', sort='-x', title=None, axis=alt.Axis(labelFont='Geist Mono', grid=False)),
            x=alt.X(f'{value}:Q', title=None, axis=alt.Axis(tickCount=5)),
            color=alt.condition(alt.datum[value] >= threshold, alt.value(CRITICAL), alt.value('#6f6f6f')),
            tooltip=[alt.Tooltip(f'{category}:N', title=category), alt.Tooltip(f'{value}:Q', title=value, format='.3f')])
        rule = alt.Chart(pd.DataFrame({'x': [threshold]})).mark_rule(color=CRITICAL, strokeDash=[4, 3], strokeWidth=1.5).encode(x='x:Q')
        st.altair_chart(_configure((bars + rule).properties(height=max(len(frame) * 26, 120))), width='stretch')


# ---- sidebar --------------------------------------------------------------------------------------
# Streamlit has no Python API to set the theme, and a page reload would end the session. The switch
# therefore drives the theme entries of Streamlit's own menu; that menu stays usable if this ever breaks.
THEME_JS = """
if (!window.taxiThemeSwitch) {
  window.taxiThemeSwitch = true;
  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-theme-choice]');
    const menu = document.querySelector('[data-testid="stMainMenu"] button');
    if (!button || !menu) return;
    document.body.classList.add('theme-switching');
    menu.click();
    const id = `[data-testid="stMainMenuItem-theme-${button.dataset.themeChoice}"]`;
    for (let i = 0; i < 40 && !document.querySelector(id); i++) await new Promise(r => setTimeout(r, 25));
    document.querySelector(id)?.click();
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    setTimeout(() => document.body.classList.remove('theme-switching'), 200);
  });
}
"""


def theme_switch():
    active = color_scheme().capitalize()
    buttons = ''.join(
        f'<button type="button" data-theme-choice="{name}"{" class=active" if name == active else ""}>{name}</button>'
        for name in ('Light', 'Dark'))
    st.html(f'<div class="switch" role="group" aria-label="Theme">{buttons}</div><script>{THEME_JS}</script>',
            unsafe_allow_javascript=True)


def system_status(firing):
    if firing is None:
        st.html('<div class="sys"><i class="dot"></i>Status unavailable</div>')
    elif firing == 0:
        st.html('<div class="sys"><i class="dot good"></i>All systems normal</div>')
    else:
        word = 'alert' if firing == 1 else 'alerts'
        st.html(f'<div class="sys"><i class="dot crit live"></i>{firing} {word} firing</div>')
