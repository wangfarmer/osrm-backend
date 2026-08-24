# EzRouting School Bus OSRM - Ubuntu 20.04 + Node Binding

This repository contains the Ubuntu 20.04 production-compatible version of the customized EzRouting / School Bus OSRM backend.

The build has been verified with:

- Ubuntu 20.04.6 LTS
- GCC 11.5.0
- G++ 9.3.0
- OSRM Backend v5.27.1
- oneTBB v2021.5.0
- Node.js v22.23.2
- npm 10.9.8
- CH preprocessing and routing
- Native Node binding (`node_osrm.node`)
- Custom School Bus logic in `src/extractor/edge_based_graph_factory.cpp`

The South Rancho regression route has been verified through both `osrm-routed` and the Node binding.

---

## 1. Verified environment

| Component | Verified version |
|---|---|
| OS | Ubuntu 20.04.6 LTS |
| C compiler | gcc 11.5.0 |
| C++ compiler | g++ 9.3.0 |
| OSRM | v5.27.1 |
| oneTBB | v2021.5.0 |
| Node.js | v22.23.2 |
| npm | 10.9.8 |
| Routing algorithm | CH |
| Lua profile | `profiles/car.lua` |

Important: the production requirement is **g++ 9.3.0**. Do not replace it with a newer g++ release when reproducing this build.

---

## 2. Important repository changes

The Ubuntu 20.04 port contains three important compatibility/customization areas.

### 2.1 School Bus CH customization

Main implementation:

```text
src/extractor/edge_based_graph_factory.cpp
```

This contains the custom School Bus edge-based graph / turnaround logic ported to OSRM v5.27.1.

The port required compatibility changes such as:

```text
TurnPenalty::value_type  -> TurnPenalty
from_alias<double>(...)  -> static_cast<double>(...)
string_view_id           -> name_id
EMPTY_STRINGVIEWID       -> EMPTY_NAMEID
```

and construction of the exiting node using the v5.27.1 graph API.

### 2.2 GCC 9 warning compatibility

File:

```text
cmake/warnings.cmake
```

Some warning switches used by newer compilers are not supported by g++ 9.3. Clang-only warning suppression is therefore guarded so GCC 9.3 does not receive unsupported options.

### 2.3 Node binding compatibility

File:

```text
src/nodejs/CMakeLists.txt
```

For Node.js 22 + NAN + g++ 9.3, the Node target needs target-specific warning handling:

```cmake
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_no_warning(node_osrm suggest-destructor-override)
endif()

target_no_warning(node_osrm suggest-override)
target_compile_options(node_osrm PRIVATE -Wno-pedantic)
```

These changes are limited to the Node/NAN build target and do not change the School Bus routing behavior.

---

## 3. Install system dependencies

Update packages:

```bash
sudo apt update
```

Install the general OSRM build dependencies used in the Ubuntu 20 environment:

```bash
sudo apt install -y \
  build-essential \
  cmake \
  pkg-config \
  git \
  curl \
  wget \
  libboost-all-dev \
  liblua5.2-dev \
  libbz2-dev \
  zlib1g-dev \
  libexpat1-dev \
  libzip-dev
```

If an old Ubuntu 20 `libtbb-dev` package is installed, remove it before installing oneTBB 2021.5.0:

```bash
sudo apt remove -y libtbb-dev
```

---

## 4. Install and pin the compilers

The verified production compiler combination is:

```text
gcc-11 = 11.5.0
g++-9  = 9.3.0
```

Install GCC 11 and the exact focal GCC 9.3 packages available in the production environment.

The GCC 9.3 packages used by the verified build are:

```text
gcc-9                 9.3.0-10ubuntu2
g++-9                 9.3.0-10ubuntu2
cpp-9                 9.3.0-10ubuntu2
gcc-9-base            9.3.0-10ubuntu2
libgcc-9-dev           9.3.0-10ubuntu2
libstdc++-9-dev        9.3.0-10ubuntu2
libasan5               9.3.0-10ubuntu2
```

After installing the exact versions, prevent accidental upgrades:

```bash
sudo apt-mark hold \
  gcc-9 \
  g++-9 \
  cpp-9 \
  gcc-9-base \
  libgcc-9-dev \
  libstdc++-9-dev \
  libasan5
```

Verify:

```bash
gcc-11 --version
g++-9 --version
```

Expected major versions:

```text
gcc 11.5.0
g++ 9.3.0
```

---

## 5. Install Node.js 22.23.2

The verified Node runtime is managed with `nvm`.

Install/use the required version:

```bash
nvm install 22.23.2
nvm use 22.23.2
nvm alias default 22.23.2
```

Verify:

```bash
node -v
npm -v
which node
which npm
```

Expected:

```text
v22.23.2
npm 10.9.8
```

The Node executable should resolve inside the user's `.nvm` directory.

---

## 6. Build oneTBB 2021.5.0

Ubuntu 20's default TBB package is too old for this OSRM build. The verified setup uses oneTBB v2021.5.0 installed under `/usr/local`.

Important: build oneTBB entirely with GCC 9 / G++ 9. Mixing gcc-11 and g++-9 caused an LTO version mismatch during the original setup.

```bash
cd ~
git clone --branch v2021.5.0 --depth 1 https://github.com/oneapi-src/oneTBB.git
cd ~/oneTBB
rm -rf build
mkdir build
cd build

export CC=/usr/bin/gcc-9
export CXX=/usr/bin/g++-9

cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DTBB_TEST=OFF \
  -DCMAKE_INSTALL_PREFIX=/usr/local \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-9 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-9

cmake --build . --parallel 4
sudo cmake --install .
sudo ldconfig
```

The verified OSRM build links against:

```text
/usr/local/lib/libtbb.so.12
```

---

## 7. OSRM version

This port is based on:

```text
OSRM v5.27.1
upstream tag commit: 4f3ee609e
```

The newer OSRM master branch was not used because it requires newer C++ language/compiler functionality and does not meet the strict g++ 9.3 production requirement.

The School Bus port was completed on the `schoolbus-ch-gpp93` development branch. The verified port commit before repository repackaging was:

```text
eb996409d  Port school bus CH modifications to OSRM v5.27.1
```

---

## 8. Build the OSRM backend

From the repository root:

```bash
cd ~/routing_algorithm/osrm-backend
```

Create a dedicated backend build directory:

```bash
rm -rf build-gpp93
mkdir build-gpp93
cd build-gpp93
```

Use the required compilers:

```bash
export CC=/usr/bin/gcc-11
export CXX=/usr/bin/g++-9
```

Configure:

```bash
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=/usr/local \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-11 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-9
```

Build the important backend executables:

```bash
cmake --build . --target osrm-extract --parallel 4
cmake --build . --target osrm-contract --parallel 4
cmake --build . --target osrm-routed --parallel 4
```

Verify:

```bash
./osrm-extract --version
./osrm-contract --version
./osrm-routed --version
```

Expected OSRM version:

```text
v5.27.1
```

---

## 9. Reference Nevada PBF

Routing output depends on the exact OSM input data. Do not assume that a newly downloaded `nevada-latest.osm.pbf` is equivalent to the verified production input.

The reference PBF used for the successful regression test has SHA256:

```text
592d6c1dada5c46ad8be6b8910583cfc283d2f72a87610a4870356a8ed171c31
```

Verify before preprocessing:

```bash
sha256sum data/nevada/nevada-latest.osm.pbf
```

The hash must match the reference value above when reproducing the verified routing result.

A newer Nevada PBF with a different hash produced a different graph representation and caused the South Rancho route to detour approximately 1 km. Replacing it with the reference PBF restored the expected route without changing C++ or Lua code.

---

## 10. Lua profile

Use:

```text
profiles/car.lua
```

Do not use a separate `schoolbus.lua` profile for this verified build.

The currently verified configuration keeps the regular `car.lua` profile and a `u_turn_penalty` of 20. The School Bus behavior under test is implemented in the C++ graph customization rather than through further Lua changes.

---

## 11. Preprocess Nevada data for CH

From the repository root:

```bash
cd ~/routing_algorithm/osrm-backend
```

Remove old generated OSRM files before changing the PBF or preprocessing logic:

```bash
rm -f data/nevada/nevada-latest.osrm*
```

Extract:

```bash
./build-gpp93/osrm-extract \
  -p profiles/car.lua \
  data/nevada/nevada-latest.osm.pbf
```

Contract for CH:

```bash
./build-gpp93/osrm-contract \
  data/nevada/nevada-latest.osrm
```

---

## 12. Run `osrm-routed`

Start the CH server:

```bash
cd ~/routing_algorithm/osrm-backend

pkill -f osrm-routed || true

nohup ./build-gpp93/osrm-routed \
  --algorithm ch \
  data/nevada/nevada-latest.osrm \
  > /tmp/osrm-routed.log 2>&1 &
```

Inspect the log:

```bash
tail -n 30 /tmp/osrm-routed.log
```

The default service is available on:

```text
http://localhost:5000
```

---

## 13. South Rancho regression test through HTTP

Coordinates:

```text
start = [-115.172776, 36.145937]
end   = [-115.173334, 36.145274]
```

Run:

```bash
curl -s \
"http://localhost:5000/route/v1/driving/-115.172776,36.145937;-115.173334,36.145274?overview=false&steps=true" \
> /tmp/rancho_route.json
```

Inspect:

```bash
python3 - <<'PY'
import json
with open('/tmp/rancho_route.json') as f:
    d = json.load(f)

r = d['routes'][0]
print('distance =', r['distance'])
print('duration =', r['duration'])
print('weight =', r['weight'])
print('\nSTEPS:')
for leg in r['legs']:
    for s in leg['steps']:
        m = s['maneuver']
        print(repr(s.get('name')), '|', m.get('type'), '|', m.get('modifier'),
              '|', s.get('distance'), '|', m.get('location'))
PY
```

Verified Ubuntu 20 result:

```text
distance = 103.1
duration = 20.5
weight = 20.5

'' | depart | right | 32.4 | [-115.17276, 36.145882]
'South Rancho Drive' | turn | left | 70.7 | [-115.173107, 36.145826]
'South Rancho Drive' | arrive | right | 0 | [-115.173198, 36.145277]
```

The important routing regression criteria are:

```text
distance = 103.1 m
main maneuver = LEFT onto South Rancho Drive
```

---

## 14. Install the Node dependencies

From the OSRM repository root:

```bash
cd ~/routing_algorithm/osrm-backend
```

Install the JavaScript dependencies without running the old package install scripts automatically:

```bash
npm install \
  --ignore-scripts \
  --no-save \
  --package-lock=false
```

The large number of deprecation/security warnings comes from the historical dependency tree in OSRM v5.27.1. Do not run `npm audit fix --force` as part of the reproducible production setup because it can replace dependencies with incompatible major versions.

---

## 15. Build the Node binding

Use a separate build directory so the normal backend build remains untouched:

```bash
cd ~/routing_algorithm/osrm-backend
rm -rf build-node-gpp93
mkdir build-node-gpp93
cd build-node-gpp93
```

Set compilers:

```bash
export CC=/usr/bin/gcc-11
export CXX=/usr/bin/g++-9
```

Configure with Node bindings enabled:

```bash
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=/usr/local \
  -DENABLE_NODE_BINDINGS=ON \
  -DCMAKE_C_COMPILER=/usr/bin/gcc-11 \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-9
```

A successful configure should contain messages similar to:

```text
-- Building node_osrm
-- NodeJS: Using node, version v22.23.2
-- Configuring node_osrm bindings for NodeJs v22.23.2
-- node_osrm artifacts will be copied to: .../lib/binding
```

Build the binding:

```bash
cmake --build . \
  --target node_osrm \
  --parallel 4
```

Expected final output:

```text
[100%] Linking CXX shared library Release/node_osrm.node
[100%] Built target node_osrm
```

Copy the binding and OSRM artifacts into `lib/binding`:

```bash
cmake --build . \
  --target copy_artifacts \
  --parallel 4
```

The resulting native module is:

```text
lib/binding/node_osrm.node
```

---

## 16. Validate the Node binding

Check for missing shared libraries:

```bash
ldd ~/routing_algorithm/osrm-backend/lib/binding/node_osrm.node \
  | grep 'not found'
```

Expected result: no output.

Load the module with Node:

```bash
cd ~/routing_algorithm/osrm-backend

node - <<'JS'
const binding = require('./lib/binding/node_osrm.node');
console.log(binding);
console.log('node_osrm loaded successfully');
JS
```

Expected shape:

```text
{ OSRM: [Function: OSRM] }
node_osrm loaded successfully
```

Important: the module exports an object containing `OSRM`. Therefore use:

```javascript
const { OSRM } = require('./lib/binding/node_osrm.node');
```

or:

```javascript
const OSRM = require('./lib/binding/node_osrm.node').OSRM;
```

Do not call `new` on the whole object returned by `require(...)`.

---

## 17. Use the Node binding for CH routing

Example:

```javascript
const { OSRM } = require('./lib/binding/node_osrm.node');

const osrm = new OSRM({
    path: './data/nevada/nevada-latest.osrm',
    algorithm: 'CH'
});

osrm.route({
    coordinates: [
        [-115.172776, 36.145937],
        [-115.173334, 36.145274]
    ],
    steps: true,
    overview: 'false'
}, (err, result) => {
    if (err) {
        console.error(err);
        process.exit(1);
    }

    const route = result.routes[0];

    console.log('distance =', route.distance);
    console.log('duration =', route.duration);
    console.log('weight =', route.weight);

    console.log('\nSTEPS:');
    for (const leg of route.legs) {
        for (const step of leg.steps) {
            console.log(
                JSON.stringify(step.name),
                '|', step.maneuver.type,
                '|', step.maneuver.modifier,
                '|', step.distance,
                '|', step.maneuver.location
            );
        }
    }
});
```

The verified Node binding result matches the successful CH route:

```text
distance = 103.1 m
South Rancho Drive = left turn
```

---

## 18. `lib/binding` artifacts

After `copy_artifacts`, the build creates a directory similar to:

```text
lib/binding/
├── node_osrm.node
├── osrm-components
├── osrm-contract
├── osrm-customize
├── osrm-datastore
├── osrm-extract
├── osrm-partition
└── osrm-routed
```

`node_osrm.node` is the native Node.js module.

If this repository is used to distribute a prebuilt Ubuntu 20 artifact, `lib/binding/node_osrm.node` may be included intentionally. Remember that it is a native Linux binary tied to the target runtime/ABI and should not be treated as a cross-platform package.

For reproducibility, the preferred source of truth remains the build process described above.

---

## 19. Web test frontend

The lightweight test frontend uses Python's standard library. Node.js is not required to serve the frontend itself.

Expected directory:

```text
web_test/
├── index.html
└── server.py
```

Run:

```bash
cd ~/routing_algorithm/web_test
python3 server.py
```

Open:

```text
http://localhost:8088
```

The frontend proxies `/osrm/...` requests to:

```text
http://localhost:5000
```

If port 8088 is already occupied:

```bash
ss -ltnp | grep 8088
pkill -f 'python3 server.py'
```

---

## 20. Why the reference PBF is pinned

During the Ubuntu 20 migration, two Nevada PBF files were found to be different.

Reference/working PBF:

```text
592d6c1dada5c46ad8be6b8910583cfc283d2f72a87610a4870356a8ed171c31
```

Newer PBF used during debugging:

```text
f9cef2c6cf6682d66a03ff1e4254a0a24cee7c36005483a036ee0664b0d85c89
```

With the newer PBF, the same endpoint snapped to a different graph representation and the route changed from approximately 103 m to approximately 1 km.

After copying the exact reference PBF into Ubuntu 20 and rerunning `osrm-extract` + `osrm-contract`, the expected 103.1 m left-turn route was restored.

Therefore:

> Do not use the filename `nevada-latest.osm.pbf` alone as a reproducibility guarantee. Always verify the file checksum.

---

## 21. Build directory convention

The verified environment uses separate directories:

```text
build-gpp93/
    normal OSRM backend binaries

build-node-gpp93/
    Node binding build

lib/binding/
    copied Node binding / OSRM runtime artifacts
```

This separation helps prevent Node-specific build changes from disturbing the normal backend build.

---

## 22. Quick end-to-end checklist

```text
[ ] Ubuntu 20.04.6
[ ] gcc-11 == 11.5.0
[ ] g++-9 == 9.3.0
[ ] GCC 9 packages held
[ ] oneTBB v2021.5.0 installed under /usr/local
[ ] Node.js v22.23.2 active
[ ] OSRM v5.27.1 source/custom port
[ ] reference Nevada PBF SHA256 matches
[ ] profiles/car.lua used
[ ] build-gpp93 succeeds
[ ] osrm-extract succeeds
[ ] osrm-contract succeeds
[ ] osrm-routed CH starts
[ ] HTTP Rancho regression = 103.1 m / LEFT
[ ] npm dependencies installed with --ignore-scripts
[ ] build-node-gpp93 configured with ENABLE_NODE_BINDINGS=ON
[ ] node_osrm target builds
[ ] copy_artifacts succeeds
[ ] ldd node_osrm.node has no missing libraries
[ ] require(node_osrm.node).OSRM loads
[ ] Node route regression = 103.1 m / LEFT
```

---

## 23. Recommended repository layout

For the Ubuntu 20 repository, keep the complete modified OSRM source tree, not only isolated patches.

A practical layout is:

```text
README.md
CMakeLists.txt
cmake/
include/
profiles/
scripts/
src/
third_party/
package.json
...
lib/
  binding/
    node_osrm.node       # optional prebuilt Ubuntu 20 artifact
web_test/
  index.html
  server.py
```

Do not commit generated routing datasets or dependency/build trees:

```text
build-gpp93/
build-node-gpp93/
node_modules/
data/**/*.osm.pbf
data/**/*.osrm*
```

The PBF checksum belongs in this README; the PBF itself should be distributed separately unless repository storage policy explicitly allows large data files.

---

## 24. Production regression route

Use this route after any compiler, OSRM, C++ logic, Node binding, or OSM data change:

```text
start: -115.172776,36.145937
end:   -115.173334,36.145274
```

Expected behavior with the reference PBF:

```text
Distance: 103.1 m
Key maneuver: LEFT onto South Rancho Drive
```

If it becomes an approximately 1 km right-turn detour, check the PBF checksum and preprocessing artifacts before changing the School Bus C++ logic.

