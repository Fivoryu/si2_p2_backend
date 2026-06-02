import urllib.request, json

data = json.dumps({'email':'carlos@mail.com','password':'password123','tenant_id':'22222222-0000-0000-0000-000000000001'}).encode()
req = urllib.request.Request('http://localhost:8000/auth/login', data=data, headers={'Content-Type':'application/json'})
r = urllib.request.urlopen(req)
conductor = json.loads(r.read().decode())
TOKEN = conductor['access_token']
headers = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}
print('CONDUCTOR LOGEADO')

data = json.dumps({'vehiculo_id': '55555555-0000-0000-0000-000000000001', 'descripcion': 'Bateria descargada', 'latitud': -17.7833, 'longitud': -63.1821}).encode()
req = urllib.request.Request('http://localhost:8000/incidentes', data=data, headers=headers)
inc = json.loads(urllib.request.urlopen(req).read().decode())
INC_ID = inc['id']
print(f'INCIDENTE CREADO: {INC_ID}')

data = json.dumps({'tipo_incidente_id': '33333333-0000-0000-0000-000000000001'}).encode()
req = urllib.request.Request(f'http://localhost:8000/incidentes/{INC_ID}/estado', data=data, headers=headers)
try:
    r = urllib.request.urlopen(req)
    print(f'CLASIFICADO: {r.read().decode()}')
except Exception as e:
    print(f'CLASIFICAR ERROR: {e}')

req = urllib.request.Request(f'http://localhost:8000/incidentes/{INC_ID}/buscar-talleres', data=b'{}', headers=headers)
cands = json.loads(urllib.request.urlopen(req).read().decode())
print(f'BUSCAR TALLERES: {cands}')

req = urllib.request.Request(f'http://localhost:8000/incidentes/{INC_ID}/asignar', data=b'{}', headers=headers)
asig = json.loads(urllib.request.urlopen(req).read().decode())
print(f'ASIGNAR: {asig}')

print('CONDUCTOR FLOW COMPLETO')
print(f'INC_ID={INC_ID}')
print(f'TOKEN={conductor["access_token"]}')