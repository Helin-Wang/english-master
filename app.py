from flask import Flask, render_template, request, redirect, url_for
import subprocess
import json
import os
import tempfile
import threading
from datetime import date

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
OCR_BIN = os.path.join(BASE_DIR, 'ocr_bin')

os.makedirs(LOGS_DIR, exist_ok=True)

MIME_EXT = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/heic': '.heic',
    'image/heif': '.heif',
    'image/png': '.png',
    'image/webp': '.webp',
}


def get_suffix(photo):
    ext = MIME_EXT.get((photo.content_type or '').lower().split(';')[0].strip(), '')
    if not ext:
        ext = os.path.splitext(photo.filename or '')[1].lower() or '.jpg'
    return ext


def ocr_image(image_path):
    result = subprocess.run(
        [OCR_BIN, image_path],
        capture_output=True, text=True, timeout=30
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def enrich_phrases(phrases, show_info):
    items = '\n'.join(f'{i+1}. {p}' for i, p in enumerate(phrases))
    prompt = (
        f'Help a Chinese student learn English from: {show_info or "a TV show"}.\n\n'
        f'Phrases:\n{items}\n\n'
        'Return ONLY a JSON array, no other text:\n'
        '[{"phrase":"...","chinese":"简洁中文释义","example":"one natural example sentence"}]\n'
        'Keep chinese concise (4-8 characters preferred). Example sentence should feel natural.'
    )
    result = subprocess.run(
        ['claude', '-p', prompt],
        capture_output=True, text=True, timeout=120
    )
    output = result.stdout.strip()
    start = output.find('[')
    end = output.rfind(']') + 1
    if start >= 0 and end > start:
        try:
            return json.loads(output[start:end])
        except json.JSONDecodeError:
            pass
    return [{'phrase': p, 'chinese': '（解析失败）', 'example': ''} for p in phrases]


def save_log(show_info, enriched):
    today = date.today().isoformat()
    log_path = os.path.join(LOGS_DIR, f'{today}.md')
    is_new = not os.path.exists(log_path)

    with open(log_path, 'a', encoding='utf-8') as f:
        if is_new:
            # YAML frontmatter for Dataview queries
            f.write(f'---\ndate: {today}\nshow: {show_info or ""}\ntags:\n  - english-learning\n---\n\n')
            # Deck tag for Obsidian Spaced Repetition plugin
            f.write('#flashcards/english\n\n')

        for item in enriched:
            f.write(f'{item["chinese"]}\n?\n{item["phrase"]}\n')
            if item.get('example'):
                f.write(f'> {item["example"]}\n')
            f.write('\n---\n\n')


def _parse_log(path):
    with open(path, encoding='utf-8') as f:
        content = f.read()
    marker = '#flashcards/english\n\n'
    if marker not in content:
        return []
    body = content[content.index(marker) + len(marker):]
    cards = []
    for block in body.split('\n---\n\n'):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if '?' not in lines:
            continue
        q = lines.index('?')
        front = '\n'.join(lines[:q]).strip()
        rest = lines[q + 1:]
        example = next((l[2:] for l in rest if l.startswith('> ')), '')
        back = '\n'.join(l for l in rest if not l.startswith('> ')).strip()
        if front and back:
            cards.append({'chinese': front, 'english': back, 'example': example})
    return cards


def build_data_json():
    data = {}
    for filename in sorted(os.listdir(LOGS_DIR)):
        if not filename.endswith('.md'):
            continue
        cards = _parse_log(os.path.join(LOGS_DIR, filename))
        if cards:
            data[filename[:-3]] = cards
    with open(os.path.join(BASE_DIR, 'data.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _git_push():
    try:
        subprocess.run(['git', '-C', BASE_DIR, 'add', 'data.json', 'logs/'], capture_output=True)
        result = subprocess.run(
            ['git', '-C', BASE_DIR, 'commit', '-m', f'update {date.today().isoformat()}'],
            capture_output=True, text=True
        )
        if 'nothing to commit' not in result.stdout:
            subprocess.run(['git', '-C', BASE_DIR, 'push'], capture_output=True)
    except Exception:
        pass


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/review', methods=['POST'])
def review():
    photos = request.files.getlist('photo')
    photos = [p for p in photos if p.filename]
    show_info = request.form.get('show_info', '').strip()
    if not photos:
        return redirect(url_for('index'))

    phrases = []
    for photo in photos:
        suffix = get_suffix(photo)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            photo.save(tmp.name)
            tmp_path = tmp.name
        try:
            phrases.extend(ocr_image(tmp_path))
        finally:
            os.unlink(tmp_path)

    return render_template('review.html', phrases=phrases, show_info=show_info)


@app.route('/enrich', methods=['POST'])
def enrich():
    phrases = [p.strip() for p in request.form.getlist('phrases') if p.strip()]
    show_info = request.form.get('show_info', '').strip()
    if not phrases:
        return redirect(url_for('index'))

    enriched = enrich_phrases(phrases, show_info)
    save_log(show_info, enriched)
    today = date.today().isoformat()
    build_data_json()
    threading.Thread(target=_git_push, daemon=True).start()
    return render_template('results.html', enriched=enriched, show_info=show_info, today=today)


if __name__ == '__main__':
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()
    print(f'\n  App started!')
    print(f'  Mac:    http://127.0.0.1:5001')
    print(f'  iPhone: http://{local_ip}:5001  (same WiFi)\n')
    app.run(host='0.0.0.0', port=5001, debug=False)
