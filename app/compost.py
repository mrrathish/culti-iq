"""Kitchen-to-compost planning, manual batch log and conservative readiness guidance."""
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.farm_features import DB

router = APIRouter(prefix='/api/compost', tags=['Kitchen compost'])
MATERIALS = {
 'vegetable_scraps': ('Vegetable peelings and scraps', 'green', 'Chop into small pieces; avoid heavily salted or oily scraps'),
 'fruit_scraps': ('Fruit peelings and scraps', 'green', 'Bury in the center to discourage fruit flies'),
 'coffee_tea': ('Coffee grounds and plain tea leaves', 'green', 'Remove plastic tea bags and staples'),
 'fresh_leaves': ('Fresh untreated leaves and grass', 'green', 'Use thin layers to avoid matting'),
 'dry_leaves': ('Dry leaves', 'brown', 'Shred if possible'),
 'cardboard': ('Plain uncoated cardboard', 'brown', 'Tear up; avoid glossy or plastic-coated material'),
 'paper': ('Plain shredded paper', 'brown', 'Avoid plastic, glossy or heavily inked paper'),
 'straw': ('Dry straw and small untreated twigs', 'brown', 'Break into small pieces'),
 'eggshells': ('Crushed eggshells', 'other', 'Rinse and crush; break down slowly'),
}
AVOID = [
 'Meat, fish, bones and dairy in a simple home compost bin',
 'Cooked oily or heavily salted leftovers',
 'Pet or human waste',
 'Diseased plant material and weeds with mature seeds in a cool home pile',
 'Plastic, glass, metal, coated packaging and synthetic tea bags',
 'Coal ash, chemically treated wood, pesticides and unknown chemicals',
]

def connect():
 DB.parent.mkdir(parents=True, exist_ok=True)
 conn = sqlite3.connect(str(DB), timeout=10)
 conn.row_factory = sqlite3.Row
 conn.execute('''CREATE TABLE IF NOT EXISTS compost_batches (
  id INTEGER PRIMARY KEY, farm_id TEXT NOT NULL, started_on TEXT NOT NULL,
  method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', notes TEXT DEFAULT '', created_at TEXT NOT NULL)''')
 conn.execute('''CREATE TABLE IF NOT EXISTS compost_events (
  id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, event_on TEXT NOT NULL,
  event_type TEXT NOT NULL, material TEXT, amount_l REAL, notes TEXT DEFAULT '',
  FOREIGN KEY(batch_id) REFERENCES compost_batches(id))''')
 conn.commit()
 return conn

class BatchRequest(BaseModel):
 farm_id: str = Field(default='demo-farm', min_length=1, max_length=80)
 started_on: date = Field(default_factory=date.today)
 method: str = Field(default='aerobic_bin', pattern='^(aerobic_bin|aerobic_pile)$')
 notes: str = Field(default='', max_length=500)

class EventRequest(BaseModel):
 event_on: date = Field(default_factory=date.today)
 event_type: str = Field(pattern='^(add|turn|moisture_check|ready_confirmed|applied)$')
 material: str | None = None
 amount_l: float | None = Field(default=None, gt=0, le=5000)
 notes: str = Field(default='', max_length=500)

@router.get('/guide')
def guide():
 return {
  'title': 'Kitchen waste to garden compost',
  'materials': [{'id': k, 'name': v[0], 'type': v[1], 'tip': v[2]} for k,v in MATERIALS.items()],
  'avoid': AVOID,
  'steps': [
   'Choose a ventilated, drainable bin or covered pile in a shaded, accessible spot; keep it away from wells and runoff channels.',
   'Start with coarse dry leaves or torn plain cardboard to maintain air spaces.',
   'Collect raw fruit and vegetable scraps in a covered kitchen caddy. Add every 1–3 days or when the caddy fills; do not leave food scraps exposed.',
   'For each container of wet green scraps, mix in roughly 2–3 containers of loose dry browns by volume as a starting point; adjust based on moisture and smell.',
   'Bury fresh scraps in the center and cap with browns to discourage pests.',
   'Check moisture weekly: material should feel like a wrung-out sponge. If soggy or smelly, add dry browns and aerate; if very dry, sprinkle water.',
   'Turn or mix about every 1–2 weeks for an actively managed aerobic pile; aeration frequency depends on the bin design and condition.',
   'Allow compost to finish and cure. Often 2–6 months for a managed home system, but climate, ingredients and turning can change this substantially.',
   'Use only when dark, crumbly, earthy-smelling, near ambient temperature, with no recognizable fresh food scraps; if uncertain, let it mature longer.',
  ],
  'application': {
   'when': 'Apply mature compost during bed preparation, usually 1–3 weeks before planting, or lightly as a surface top-dress during the growing season.',
   'amount': 'For an established vegetable bed, start conservatively with about 1–2 cm of mature compost as a surface layer (10–20 L/m²). Incorporate shallowly if appropriate; avoid direct contact with stems.',
   'repeat': 'Typically once before each major planting cycle; an additional light top-dress may be used during a long crop cycle if soil conditions justify it. Avoid repeated heavy applications.',
   'caution': 'These are broad home-garden starting points, not a soil-test-based nutrient prescription. Excess compost can build up salts or phosphorus; use local soil tests for ongoing application rates.',
  },
  'safety': 'Use plant-based household compost only. Wash hands, control pests, and keep fresh or unfinished material away from edible leaves and harvestable produce. Do not apply immature compost directly to crops.'
 }

@router.post('/batches')
def create_batch(payload: BatchRequest):
 with connect() as c:
  cur=c.execute('INSERT INTO compost_batches(farm_id,started_on,method,notes,created_at) VALUES(?,?,?,?,?)',
    (payload.farm_id,payload.started_on.isoformat(),payload.method,payload.notes,datetime.now(timezone.utc).isoformat()))
  return {'id':cur.lastrowid,'created':True,'message':'Batch created. Add materials and record turning or moisture checks.'}

@router.get('/batches')
def list_batches(farm_id: str='demo-farm'):
 with connect() as c:
  rows=c.execute('SELECT * FROM compost_batches WHERE farm_id=? ORDER BY id DESC LIMIT 100',(farm_id,)).fetchall()
  result=[]
  today=date.today()
  for row in rows:
   batch=dict(row)
   events=c.execute('SELECT event_on,event_type,material,amount_l,notes FROM compost_events WHERE batch_id=? ORDER BY event_on DESC,id DESC LIMIT 100',(row['id'],)).fetchall()
   history=[dict(e) for e in events]
   started=date.fromisoformat(row['started_on'])
   last_turn=next((date.fromisoformat(e['event_on']) for e in history if e['event_type']=='turn'),started)
   last_check=next((date.fromisoformat(e['event_on']) for e in history if e['event_type']=='moisture_check'),started)
   batch.update({'events':history,'age_days':max(0,(today-started).days),
    'next_turn_check_on':(last_turn+timedelta(days=10)).isoformat(),
    'next_moisture_check_on':(last_check+timedelta(days=7)).isoformat(),
    'readiness':'Readiness cannot be determined from age alone; inspect smell, texture, temperature and recognizable scraps.'})
   result.append(batch)
 return {'batches':result,'note':'Dates are guidance for manual checks, not scheduled notifications.'}

@router.post('/batches/{batch_id}/events')
def log_event(batch_id:int, payload:EventRequest):
 if payload.event_type=='add' and payload.material not in MATERIALS:
  raise HTTPException(422,'Select an allowed compost material')
 if payload.event_type!='add' and payload.material:
  raise HTTPException(422,'Material is only valid for add events')
 with connect() as c:
  row=c.execute('SELECT id,status,started_on FROM compost_batches WHERE id=?',(batch_id,)).fetchone()
  if not row:raise HTTPException(404,'Compost batch not found')
  if payload.event_on < date.fromisoformat(row['started_on']):raise HTTPException(422,'Event date is before batch start')
  if payload.event_on > date.today():raise HTTPException(422,'Cannot log a future event')
  if row['status']=='finished' and payload.event_type not in ('applied',):raise HTTPException(422,'This batch is finished')
  c.execute('INSERT INTO compost_events(batch_id,event_on,event_type,material,amount_l,notes) VALUES(?,?,?,?,?,?)',
   (batch_id,payload.event_on.isoformat(),payload.event_type,payload.material,payload.amount_l,payload.notes))
  if payload.event_type=='ready_confirmed':
   c.execute('UPDATE compost_batches SET status=? WHERE id=?',('finished',batch_id))
 return {'saved':True,'batch_id':batch_id,'event_type':payload.event_type,
  'warning':'Readiness must be checked by a person; this event does not certify compost safety.' if payload.event_type=='ready_confirmed' else None}
