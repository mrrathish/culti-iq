import io, json, os, math, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image

app = FastAPI(title='CultiIQ - Sustainable Farm Intelligence', version='0.1.0')
STATIC = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')
MAX_BYTES = 8 * 1024 * 1024

CROPS = [
    {'name':'Tomato','sun_min':6,'water_mm':3.4,'group':'fruiting','score':8},
    {'name':'Chilli','sun_min':6,'water_mm':3.4,'group':'fruiting','score':7},
    {'name':'Okra','sun_min':6,'water_mm':3.3,'group':'fruiting','score':8},
    {'name':'Brinjal','sun_min':6,'water_mm':3.3,'group':'fruiting','score':7},
    {'name':'Beans','sun_min':6,'water_mm':3.3,'group':'legume','score':8},
    {'name':'Palak','sun_min':4,'water_mm':2.5,'group':'leafy','score':7},
    {'name':'Coriander','sun_min':4,'water_mm':2.5,'group':'leafy','score':7},
    {'name':'Amaranth','sun_min':4,'water_mm':2.5,'group':'leafy','score':8},
    {'name':'Marigold','sun_min':6,'water_mm':2.3,'group':'flower','score':6},
]

class Bed(BaseModel):
    id: str
    area_sqft: float = Field(gt=0, le=100000)
    sun_hours: float = Field(ge=0, le=16)
    soil: str = 'unknown'

class PlanRequest(BaseModel):
    beds: list[Bed] = Field(min_length=1, max_length=50)
    water_lpd: float = Field(gt=0, le=1000000)
    reserve_pct: float = Field(default=10, ge=0, le=50)
    irrigation_efficiency: float = Field(default=0.9, gt=0, le=1)
    excluded_crops: list[str] = []

class SimulationRequest(BaseModel):
    baseline_water_lpd: float = Field(default=3000, gt=0)
    reduction_pct: float = Field(default=30, ge=0, lt=100)
    reserve_pct: float = Field(default=10, ge=0, le=50)
    beds: Optional[list[Bed]] = None

@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html')

@app.get('/healthz')
def healthz():
    return {'status':'ok','service':'cultiiq','ai_enabled':bool(os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('GEMINI_API_KEY'))}

@app.get('/api/crops')
def crops():
    return {'crops':CROPS}

@app.post('/api/plan')
def plan(req: PlanRequest):
    # CP-SAT assigns at most one crop to each bed, allows a bed to remain unplanted,
    # and honors sunlight and a farm-wide daily water constraint.
    from ortools.sat.python import cp_model
    model=cp_model.CpModel()
    choices={}
    allowed=[c for c in CROPS if c['name'].lower() not in {x.lower() for x in req.excluded_crops}]
    if not allowed: raise HTTPException(422,'All crops excluded')
    for i,b in enumerate(req.beds):
        for j,c in enumerate(allowed):
            if b.sun_hours >= c['sun_min']:
                choices[i,j]=model.NewBoolVar(f'b{i}_c{j}')
        model.Add(sum(v for (bi,j),v in choices.items() if bi==i)<=1)
    # Water coefficients in millilitres/day: area sqft -> m2, mm -> L/m2
    cap_ml=round(req.water_lpd*(1-req.reserve_pct/100)*1000)
    model.Add(sum(round(req.beds[i].area_sqft*0.09290304*c['water_mm']/req.irrigation_efficiency*1000)*v
                  for (i,j),v in choices.items() for c in [allowed[j]])<=cap_ml)
    # Objective favors planted area and diverse crops, not validated yields or profit.
    model.Maximize(sum(round(req.beds[i].area_sqft*10)*allowed[j]['score']*v for (i,j),v in choices.items()))
    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=5
    status=solver.Solve(model)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE): raise HTTPException(422,'No feasible allocation')
    allocations=[]; total=0
    for i,b in enumerate(req.beds):
        matched=[allowed[j] for (bi,j),v in choices.items() if bi==i and solver.Value(v)]
        c=matched[0] if matched else None
        liters=round(b.area_sqft*0.09290304*c['water_mm']/req.irrigation_efficiency,1) if c else 0
        total+=liters
        allocations.append({'bed':b.id,'area_sqft':b.area_sqft,'sun_hours':b.sun_hours,
                            'crop':c['name'] if c else 'Rest / alternative use', 'estimated_lpd':liters})
    return {'status':'optimized' if status==cp_model.OPTIMAL else 'feasible',
            'allocation':allocations,'estimated_irrigation_lpd':round(total,1),
            'usable_water_lpd':round(req.water_lpd*(1-req.reserve_pct/100),1),
            'headroom_lpd':round(req.water_lpd*(1-req.reserve_pct/100)-total,1),
            'method':'OR-Tools CP-SAT with illustrative crop water depths and sunlight thresholds',
            'warning':'Prototype model only: validate local ET0, crop stage, soil, rainfall, planting density, disease rotation and yield before field use.'}

@app.post('/api/simulate')
def simulate(req: SimulationRequest):
    supply=req.baseline_water_lpd*(1-req.reduction_pct/100)
    beds=req.beds or [Bed(id='A - sunny',area_sqft=1300,sun_hours=8),Bed(id='B - leafy',area_sqft=1500,sun_hours=5),
                      Bed(id='C - sunny',area_sqft=1300,sun_hours=7),Bed(id='D - sunny',area_sqft=1100,sun_hours=7),
                      Bed(id='E - mixed',area_sqft=1700,sun_hours=6)]
    result=plan(PlanRequest(beds=beds,water_lpd=supply,reserve_pct=req.reserve_pct))
    return {'baseline_lpd':req.baseline_water_lpd,'reduced_supply_lpd':round(supply,1),
            'operational_budget_lpd':round(supply*(1-req.reserve_pct/100),1),**result}

async def read_image(file: UploadFile):
    data=await file.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES: raise HTTPException(413,'Image must be at most 8 MB')
    try:
        img=Image.open(io.BytesIO(data)); img.verify()
        img=Image.open(io.BytesIO(data)).convert('RGB')
        if img.width*img.height>20_000_000: raise ValueError('Image too large')
        out=io.BytesIO(); img.thumbnail((1600,1600)); img.save(out,format='JPEG',quality=82)
        return out.getvalue()
    except Exception:
        raise HTTPException(415,'Upload a valid JPEG, PNG or WebP image')

def parse_json(s):
    s=re.sub(r'^```(?:json)?\s*|\s*```$', '', s.strip(),flags=re.I)
    try: return json.loads(s)
    except Exception: return {'raw_response':s,'requires_review':True}

def analyze_with_gemini(image:bytes, prompt:str):
    # Prefer Vertex AI workload identity on GCE; API key supported for local prototyping.
    from google import genai
    from google.genai import types
    project=os.getenv('GOOGLE_CLOUD_PROJECT'); key=os.getenv('GEMINI_API_KEY')
    if not project and not key:
        raise HTTPException(503,'AI is not configured. Set GOOGLE_CLOUD_PROJECT and grant Vertex AI access, or set GEMINI_API_KEY.')
    try:
        client=(genai.Client(vertexai=True,project=project,location=os.getenv('GOOGLE_CLOUD_LOCATION','global'))
                if project else genai.Client(api_key=key))
        response=client.models.generate_content(model=os.getenv('GEMINI_MODEL','gemini-2.5-flash'),
            contents=[types.Part.from_bytes(data=image,mime_type='image/jpeg'),prompt],
            config=types.GenerateContentConfig(response_mime_type='application/json',temperature=0.1))
        return parse_json(response.text or '{}')
    except Exception as e:
        raise HTTPException(502,f'Gemini request failed: {type(e).__name__}. Check project, model, region, IAM and API enablement.')

@app.post('/api/vision/{mode}')
async def vision(mode:str,file:UploadFile=File(...),bed_id:str=Form('unassigned'),sensor_model:str=Form('unknown')):
    if mode not in ('sensor','crop','compound','soil'):
        raise HTTPException(404,'Supported modes: sensor, crop, compound, soil')
    img=await read_image(file)
    common='Return a single JSON object. Do not invent measurements or precise physical dimensions. Clearly separate observations, hypotheses and unknowns.'
    prompts={
      'sensor':f'{common} Read the soil meter display or dial. Sensor model supplied: {sensor_model}. Return fields sensor_type, measurement_mode, displayed_value (number or null), displayed_unit, scale_min, scale_max, needle_region, legible (boolean), ambiguity, requires_user_confirmation (true). Do not assume a percent display means volumetric water content.',
      'crop':f'{common} Inspect crop in bed {bed_id}. Return crop_guess, visible_symptoms (array), possible_causes (array), confidence_limitations, suggested_followup_observations (array), urgent_warning, and action_requires_expert_confirmation (true). Never claim a confirmed disease from the photo alone. No pesticide dosages.',
      'compound':f'{common} Describe a potential vegetable garden site. Return visible_open_areas, obstacles, visible_shade, approximate_sun_exposure_only (no fabricated hours), information_needed (array including plot dimensions, north orientation, wall/tree heights, location, season), and conceptual_bed_suggestions (array). A single image cannot determine seasonal sunlight or exact measurements.',
      'soil':f'{common} Describe only visible soil surface characteristics. Return surface_appearance, visible_cracking, pooling, vegetation_stress, uncertainty, additional_measurements_needed. Never report measured root-zone moisture from an image.'}
    result=analyze_with_gemini(img,prompts[mode])
    return {'mode':mode,'bed_id':bed_id,'observed_at':datetime.now(timezone.utc).isoformat(),
            'analysis':result,'verified':False,'farmer_confirmation_required':True}

class RecoveryPreviewRequest(BaseModel):
    bed_id: str = Field(default='A', max_length=40)
    crop: str = Field(default='Cabbage', max_length=80)
    affected_plants: int = Field(default=1, ge=0, le=100000)
    total_plants: int = Field(default=20, ge=1, le=100000)
    condition: str = Field(default='unconfirmed', pattern='^(unconfirmed|suspected|expert_confirmed)$')
    spread_observed: bool = False
    baseline_water_lpd: float = Field(default=3000, gt=0, le=1000000)
    reduction_pct: float = Field(default=30, ge=0, lt=100)
    reserve_pct: float = Field(default=10, ge=0, le=50)

@app.post('/api/recovery-preview')
def recovery_preview(req: RecoveryPreviewRequest):
    if req.affected_plants > req.total_plants:
        raise HTTPException(422,'Affected plants cannot exceed total plants')
    sim=simulate(SimulationRequest(baseline_water_lpd=req.baseline_water_lpd,
                                  reduction_pct=req.reduction_pct,reserve_pct=req.reserve_pct))
    ratio=round(req.affected_plants/req.total_plants*100,1)
    actions=[
      {'priority':'Now','action':'Record affected plants, symptoms and photographs; avoid consuming visibly decayed produce','owner':'Farmer'},
      {'priority':'Next','action':'Inspect nearby plants and note moisture, odor, texture, recent rainfall and drainage','owner':'Farmer'},
      {'priority':'Before treatment','action':'Seek qualified local agricultural advice for diagnosis and any removal or treatment decisions','owner':'Expert'},
      {'priority':'Planning','action':'Review water allocation and planting options under the revised supply constraint','owner':'CultiIQ'}]
    if req.spread_observed:
        actions.insert(1,{'priority':'High','action':'Prioritize checking neighboring beds; document extent and contact an agricultural extension specialist','owner':'Farmer / Expert'})
    return {'bed_id':req.bed_id,'crop':req.crop,'condition':req.condition,
            'affected_fraction_pct':ratio,'affected_count':req.affected_plants,'total_count':req.total_plants,
            'field_assessment':'Expert confirmed' if req.condition=='expert_confirmed' else 'Unverified farmer observation',
            'recovery_actions':actions,'water_scenario':sim,
            'modeling_notes':['Affected fraction is a count-based observation, not a predicted yield loss.',
                              'Optimization uses illustrative crop coefficients and sample beds.',
                              'No disease identification, pesticide treatment, yield or sustainability improvement is certified.']}

from app.farm_features import router as farm_router
app.include_router(farm_router)

from app.compost import router as compost_router
app.include_router(compost_router)

from app.studio_v5 import router as studio_v5_router
app.include_router(studio_v5_router)

@app.get('/studio')
def studio_home():
    return FileResponse(STATIC / 'v5.html')

from app.auth_v8 import router as auth_v8_router
app.include_router(auth_v8_router)
