"""Reads what an uploaded evidence file says about itself and turns it into
advisory tags for the reviewer.

Nothing here verifies a document. File metadata is easy to strip or forge, so
every tag is a prompt to look closer, never a finding, and a failure to read a
file yields no tags rather than a refused upload.
"""
import hashlib
import io
import re
import zipfile
from datetime import datetime, timedelta, timezone as tz
from pathlib import Path

from django.utils import timezone

# Tools whose output is a composed or altered file rather than a scan, an
# export from the issuing system or a photograph.
EDITORS = (
    'photoshop', 'illustrator', 'indesign', 'gimp', 'canva', 'inkscape', 'coreldraw', 'affinity',
    'pixelmator', 'paint.net', 'picsart', 'snapseed', 'lightroom', 'figma',
    'ilovepdf', 'smallpdf', 'sejda', 'pdfescape', 'pdf-xchange', 'phantompdf', 'foxit pdf editor',
    'nitro', 'pdffiller', 'dochub', 'pdfelement', 'pdf24', 'soda pdf', 'xodo', 'pdf expert',
)
SCANNERS = ('scan', 'camscanner', 'adobe scan', 'office lens', 'genius scan', 'tiny scanner', 'epson', 'canon',
            'fujitsu', 'hp scan', 'kyocera', 'ricoh', 'xerox', 'brother', 'konica', 'sharp')
SIGNATURES = {
    '.pdf': (b'%PDF-',), '.png': (b'\x89PNG\r\n\x1a\n',), '.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',),
    '.docx': (b'PK\x03\x04',), '.xlsx': (b'PK\x03\x04',), '.zip': (b'PK\x03\x04', b'PK\x05\x06'),
    '.doc': (b'\xd0\xcf\x11\xe0',),
}
PDF_FIELDS = {'created': (b'CreationDate', b'xmp:CreateDate'), 'modified': (b'ModDate', b'xmp:ModifyDate'),
              'producer': (b'Producer', b'pdf:Producer'), 'software': (b'Creator', b'xmp:CreatorTool'),
              'author': (b'Author', None)}


def _text(raw):
    if raw.startswith(b'\xfe\xff'):
        return raw[2:].decode('utf-16-be', 'ignore').strip()
    return raw.decode('latin-1', 'ignore').strip()


def _when(value):
    """PDF (D:20240131120000+01'00'), EXIF (2024:01:31 12:00:00) and ISO dates, as an aware ISO string."""
    digits = re.match(r"\D*(\d{4})\D?(\d{2})\D?(\d{2})(?:\D?(\d{2})\D?(\d{2})(?:\D?(\d{2}))?)?", value or '')
    if not digits:
        return None
    try:
        parts = [int(part) if part else 0 for part in digits.groups()]
        return datetime(*parts, tzinfo=tz.utc).isoformat()
    except ValueError:
        return None


def _pdf(data):
    found = {'pages': len(re.findall(rb'/Type\s*/Page(?![s\w])', data)),
             'revisions': data.count(b'%%EOF'), 'encrypted': b'/Encrypt' in data,
             'has_text': b'/Font' in data}
    for key, (info, xmp) in PDF_FIELDS.items():
        match = re.search(rb'/' + info + rb'\s*\(((?:\\.|[^\\)]){0,300})\)', data, re.S)
        value = _text(re.sub(rb'\\(.)', rb'\1', match.group(1), flags=re.S)) if match else ''
        if not value:
            match = re.search(rb'/' + info + rb'\s*<([0-9A-Fa-f\s]{2,600})>', data)
            if match:
                try:
                    value = _text(bytes.fromhex(re.sub(rb'\s', b'', match.group(1)).decode()))
                except ValueError:
                    value = ''
        if not value and xmp:
            match = re.search(rb'<' + xmp + rb'>\s*([^<]{1,300})<', data) or re.search(xmp + rb'="([^"]{1,300})"', data)
            value = match.group(1).decode('utf-8', 'ignore').strip() if match else ''
        if value:
            found[key] = _when(value) if key in ('created', 'modified') else value[:200]
    return found


def _image(data):
    from PIL import ExifTags, Image
    with Image.open(io.BytesIO(data)) as image:
        found = {'width': image.width, 'height': image.height}
        exif = image.getexif()
        detail = exif.get_ifd(ExifTags.IFD.Exif)
        software = exif.get(ExifTags.Base.Software) or image.info.get('Software')
        camera = ' '.join(str(part).strip('\x00 ') for part in [exif.get(ExifTags.Base.Make), exif.get(ExifTags.Base.Model)] if part)
        taken = detail.get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
        if software:
            found['software'] = str(software).strip('\x00 ')[:200]
        if camera:
            found['camera'] = camera[:200]
        if taken:
            found['created'] = _when(str(taken))
        found['has_exif'] = bool(len(exif))
        found['has_location'] = bool(exif.get_ifd(ExifTags.IFD.GPSInfo))
    return found


def _office(data):
    found = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        found['entries'] = len(names)
        fields = {'docProps/core.xml': {'author': 'dc:creator', 'modified_by': 'cp:lastModifiedBy',
                                        'created': 'dcterms:created', 'modified': 'dcterms:modified'},
                  'docProps/app.xml': {'software': 'Application'}}
        for name, tags in fields.items():
            if name not in names or archive.getinfo(name).file_size > 1024 * 1024:
                continue
            xml = archive.read(name).decode('utf-8', 'ignore')
            for key, tag in tags.items():
                match = re.search(rf'<{tag}[^>]*>([^<]{{1,300}})</{tag}>', xml)
                if match:
                    value = match.group(1).strip()
                    found[key] = _when(value) if key in ('created', 'modified') else value[:200]
    return found


def inspect(upload):
    """What the file itself carries. Always returns a dict; unreadable files just carry less."""
    upload.seek(0)
    data = upload.read()
    upload.seek(0)
    suffix = Path(upload.name).suffix.lower()
    found = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'extension': suffix,
             'signature_matches': data.startswith(SIGNATURES.get(suffix, (b'',)))}
    if not found['signature_matches']:
        return found
    try:
        if suffix == '.pdf':
            found.update(_pdf(data))
        elif suffix in ('.jpg', '.jpeg', '.png'):
            found.update(_image(data))
        elif suffix in ('.docx', '.xlsx', '.zip'):
            found.update(_office(data))
    except Exception:  # A malformed file is the reviewer's to judge, not a reason to fail the upload.
        found['unreadable'] = True
    return {key: value for key, value in found.items() if value is not None}


def _tag(code, tone, label, detail):
    return {'code': code, 'tone': tone, 'label': label, 'detail': detail}


def _day(value):
    return datetime.fromisoformat(value).date() if value else None


def advise(found, *, issued_on=None, duplicates=()):
    """Advisory tags for the reviewer, most significant first."""
    tags = []
    suffix = found.get('extension', '')
    tools = ' / '.join(dict.fromkeys(str(found[key]) for key in ('software', 'producer') if found.get(key)))
    created, modified, today = _day(found.get('created')), _day(found.get('modified')), timezone.localdate()

    if not found.get('signature_matches', True):
        tags.append(_tag('type_mismatch', 'warning', 'Not a real ' + suffix.lstrip('.').upper(),
                         'The file content does not match its extension. Open it with care and ask for a proper copy.'))
    if found.get('unreadable'):
        tags.append(_tag('unreadable', 'warning', 'Could not be read', 'The file is damaged or malformed. Confirm it opens before relying on it.'))
    for title in duplicates:
        tags.append(_tag('duplicate', 'warning', 'Same file as another upload',
                         f'This is byte-for-byte the file submitted as "{title}". Check each requirement has its own evidence.'))
    editor = next((name for name in EDITORS if name in tools.lower()), None)
    if editor:
        tags.append(_tag('editing_software', 'warning', 'Made with editing software',
                         f'The file records "{tools}". Compare it against the issuer\'s original or verify with the issuer.'))
    if created and issued_on and created < issued_on - timedelta(days=1):
        tags.append(_tag('predates_issue', 'warning', 'File older than stated issue date',
                         f'The file was created on {created}, before the issue date given ({issued_on}). Confirm the dates on the document.'))
    if any(day and day > today + timedelta(days=1) for day in (created, modified)):
        tags.append(_tag('future_date', 'warning', 'File dated in the future', 'The file carries a date later than today, which suggests a wrong clock or altered metadata.'))
    if created and modified and modified - created > timedelta(days=1):
        tags.append(_tag('modified_later', 'warning', 'Changed after it was created',
                         f'Created {created}, last changed {modified}. Check the content for alterations.'))
    elif suffix == '.pdf' and found.get('revisions', 0) > 1:
        tags.append(_tag('resaved', 'info', 'Saved more than once', 'The PDF holds more than one saved revision. This is common for signed or annotated files.'))
    if found.get('encrypted'):
        tags.append(_tag('encrypted', 'info', 'Password-protected', 'The PDF is encrypted, so it may not open or print without a password.'))
    readable = found.get('signature_matches', True) and not found.get('unreadable')
    if readable and suffix in ('.docx', '.doc', '.xlsx'):
        tags.append(_tag('editable_source', 'info', 'Editable file, not a signed copy',
                         'An office file can be changed by anyone. Ask for the signed or issued copy where the requirement is a certificate.'))
    if readable and suffix == '.zip':
        tags.append(_tag('archive', 'info', f'Archive of {found.get("entries", 0)} file(s)', 'Open the archive and review each file inside it.'))

    if readable and suffix == '.pdf':
        if any(name in tools.lower() for name in SCANNERS) or (found.get('pages') and not found.get('has_text')):
            tags.append(_tag('scan', 'info', 'Scanned copy', 'The PDF looks like a scan, so its text cannot be searched. Read the dates and numbers off the page.'))
        if not any(found.get(key) for key in ('created', 'modified', 'producer', 'software')):
            tags.append(_tag('no_metadata', 'info', 'No file metadata', 'The PDF carries no creation details. They may have been removed.'))
    if suffix in ('.jpg', '.jpeg', '.png') and found.get('width'):
        if found.get('camera'):
            tags.append(_tag('photo', 'info', 'Photograph', f'Taken with {found["camera"]}' + (f' on {created}.' if created else '.')))
        elif not found.get('has_exif'):
            tags.append(_tag('no_metadata', 'info', 'No camera metadata', 'Likely a screenshot or an image whose details were removed, not a direct photo or scan.'))
        if max(found['width'], found.get('height', 0)) < 1000:
            tags.append(_tag('low_resolution', 'warning', 'Low resolution',
                             f'{found["width"]} x {found["height"]} pixels. Small print may not be legible; ask for a clearer copy if so.'))
    if created and not any(tag['code'] in ('predates_issue', 'future_date') for tag in tags) and today - created <= timedelta(days=1):
        tags.append(_tag('fresh', 'info', 'File created at upload time', 'Normal for a new scan or photo; worth a look if this is meant to be an original digital certificate.'))
    return sorted(tags, key=lambda tag: tag['tone'] != 'warning')
