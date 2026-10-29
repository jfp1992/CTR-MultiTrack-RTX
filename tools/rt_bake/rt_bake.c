/*
 * rt_bake: offline path tracer for CTR level vertex lighting.
 *
 * Driven by scripts/rt_bake.py. Reads one scene file, writes per-vertex
 * RGB irradiance (float32 x3 per vertex). Y is up (CTR gravity is -Y).
 *
 * Input (little endian):
 *   u32 magic 'RTBK', u32 nv, u32 nt
 *   f32 sun_dir[3] (towards the sun, normalised), f32 sun_col[3], f32 sun_cone (radians)
 *   f32 sky_zenith[3], f32 sky_horizon[3], f32 ground_col[3]
 *   u32 samples, u32 sun_samples, u32 bounces, f32 eps, f32 ao_radius
 *   f32 pos[nv][3], f32 nrm[nv][3] (0,0,0 = vertex not lit), f32 albedo[nv][3]
 *   u32 tri[nt][3]   (occluders; front face = same winding as nrm)
 *   u32 nlights, u32 light_samples
 *   per light: f32 base[3], f32 height, f32 col[3], f32 falloff, f32 size
 *
 * Point lights (indoor tracks) hang `height` above `base`, or 60% of the way
 * to the ceiling when that is lower. Irradiance from one:
 *   col * max(0, n.L) * 1 / (1 + (d / falloff)^2), cut off smoothly at 6 falloff,
 * times the fraction of light_samples shadow rays to points of a `size` sphere
 * that get through.
 *
 * ao_radius > 0: hemisphere rays only count hits closer than that (ambient
 * occlusion; indoor tracks, where there is no sky to see).
 *
 * Lighting: pass 0 = sun + sky through the hemisphere; pass k adds one
 * bounce of light, using pass k-1's vertex irradiance interpolated at each
 * hit point and the hit's albedo. Every pass retraces the same rays (same
 * per-vertex seed), so the result is deterministic.
 *
 * build: cc -O2 -fopenmp -o rt_bake rt_bake.c -lm
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { float x, y, z; } v3;

static v3 add(v3 a, v3 b) { return (v3){a.x + b.x, a.y + b.y, a.z + b.z}; }
static v3 sub(v3 a, v3 b) { return (v3){a.x - b.x, a.y - b.y, a.z - b.z}; }
static v3 mul(v3 a, float s) { return (v3){a.x * s, a.y * s, a.z * s}; }
static v3 vmul(v3 a, v3 b) { return (v3){a.x * b.x, a.y * b.y, a.z * b.z}; }
static float dot(v3 a, v3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static v3 cross(v3 a, v3 b) { return (v3){a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
static v3 norm(v3 a) { float l = sqrtf(dot(a, a)); return l > 0 ? mul(a, 1.0f / l) : a; }
static float axis(v3 a, int k) { return k == 0 ? a.x : k == 1 ? a.y : a.z; }
static v3 vmin(v3 a, v3 b) { return (v3){fminf(a.x, b.x), fminf(a.y, b.y), fminf(a.z, b.z)}; }
static v3 vmax(v3 a, v3 b) { return (v3){fmaxf(a.x, b.x), fmaxf(a.y, b.y), fmaxf(a.z, b.z)}; }

/* ---- scene ---- */

static uint32_t nv, nt;
static v3 *pos, *nrm, *alb;
static uint32_t (*tri)[3];

/* ---- BVH (binned SAH) ---- */

typedef struct { v3 lo, hi; uint32_t left_or_first, count; } Node; /* count 0 = inner, children left, left+1 */

static Node *nodes;
static uint32_t nnodes, *tidx;
static v3 *cen;

static float area(v3 lo, v3 hi) { v3 d = sub(hi, lo); return d.x * d.y + d.y * d.z + d.z * d.x; }

static void bounds(Node *n)
{
    n->lo = (v3){1e30f, 1e30f, 1e30f};
    n->hi = (v3){-1e30f, -1e30f, -1e30f};
    for (uint32_t i = 0; i < n->count; i++) {
        uint32_t *t = tri[tidx[n->left_or_first + i]];
        for (int k = 0; k < 3; k++) {
            n->lo = vmin(n->lo, pos[t[k]]);
            n->hi = vmax(n->hi, pos[t[k]]);
        }
    }
}

#define BINS 16

static void split(uint32_t ni)
{
    Node *n = &nodes[ni];
    if (n->count <= 2)
        return;
    float best = 1e30f;
    int best_ax = -1, best_bin = 0;
    float best_lo = 0, best_scale = 0;
    for (int ax = 0; ax < 3; ax++) {
        float lo = 1e30f, hi = -1e30f;
        for (uint32_t i = 0; i < n->count; i++) {
            float c = axis(cen[tidx[n->left_or_first + i]], ax);
            lo = fminf(lo, c);
            hi = fmaxf(hi, c);
        }
        if (hi - lo < 1e-6f)
            continue;
        struct { v3 lo, hi; uint32_t n; } bin[BINS];
        for (int b = 0; b < BINS; b++)
            bin[b].lo = (v3){1e30f, 1e30f, 1e30f}, bin[b].hi = (v3){-1e30f, -1e30f, -1e30f}, bin[b].n = 0;
        float scale = BINS / (hi - lo);
        for (uint32_t i = 0; i < n->count; i++) {
            uint32_t ti = tidx[n->left_or_first + i];
            int b = (int)((axis(cen[ti], ax) - lo) * scale);
            if (b >= BINS) b = BINS - 1;
            bin[b].n++;
            for (int k = 0; k < 3; k++) {
                bin[b].lo = vmin(bin[b].lo, pos[tri[ti][k]]);
                bin[b].hi = vmax(bin[b].hi, pos[tri[ti][k]]);
            }
        }
        float la[BINS - 1], ra[BINS - 1];
        uint32_t lc[BINS - 1], rc[BINS - 1];
        v3 l0 = {1e30f, 1e30f, 1e30f}, l1 = {-1e30f, -1e30f, -1e30f}, r0 = l0, r1 = l1;
        uint32_t ls = 0, rs = 0;
        for (int b = 0; b < BINS - 1; b++) {
            ls += bin[b].n, lc[b] = ls;
            l0 = vmin(l0, bin[b].lo), l1 = vmax(l1, bin[b].hi), la[b] = ls ? area(l0, l1) : 0;
            int r = BINS - 1 - b;
            rs += bin[r].n, rc[r - 1] = rs;
            r0 = vmin(r0, bin[r].lo), r1 = vmax(r1, bin[r].hi), ra[r - 1] = rs ? area(r0, r1) : 0;
        }
        for (int b = 0; b < BINS - 1; b++) {
            float cost = lc[b] * la[b] + rc[b] * ra[b];
            if (lc[b] && rc[b] && cost < best)
                best = cost, best_ax = ax, best_bin = b, best_lo = lo, best_scale = scale;
        }
    }
    if (best_ax < 0 || best >= n->count * area(n->lo, n->hi))
        return;
    uint32_t i = n->left_or_first, j = i + n->count - 1;
    while (i <= j) {
        int b = (int)((axis(cen[tidx[i]], best_ax) - best_lo) * best_scale);
        if (b >= BINS) b = BINS - 1;
        if (b <= best_bin) {
            i++;
        } else {
            uint32_t t = tidx[i]; tidx[i] = tidx[j]; tidx[j] = t;
            if (j-- == 0) break;
        }
    }
    uint32_t lcount = i - n->left_or_first;
    if (lcount == 0 || lcount == n->count)
        return;
    uint32_t l = nnodes;
    nnodes += 2;
    nodes[l] = (Node){.left_or_first = n->left_or_first, .count = lcount};
    nodes[l + 1] = (Node){.left_or_first = i, .count = n->count - lcount};
    n->left_or_first = l;
    n->count = 0;
    bounds(&nodes[l]);
    bounds(&nodes[l + 1]);
    split(l);
    split(l + 1);
}

static void build_bvh(void)
{
    nodes = malloc(sizeof(Node) * (2 * nt + 1));
    tidx = malloc(sizeof(uint32_t) * nt);
    cen = malloc(sizeof(v3) * nt);
    for (uint32_t i = 0; i < nt; i++) {
        tidx[i] = i;
        cen[i] = mul(add(add(pos[tri[i][0]], pos[tri[i][1]]), pos[tri[i][2]]), 1.0f / 3);
    }
    nnodes = 1;
    nodes[0] = (Node){.left_or_first = 0, .count = nt};
    bounds(&nodes[0]);
    split(0);
}

/* ---- tracing ---- */

typedef struct { float t, u, v; int32_t tri; } Hit;

static int box_hit(const Node *n, v3 o, v3 inv, float tmax)
{
    float t0 = (n->lo.x - o.x) * inv.x, t1 = (n->hi.x - o.x) * inv.x;
    float tn = fminf(t0, t1), tf = fmaxf(t0, t1);
    t0 = (n->lo.y - o.y) * inv.y, t1 = (n->hi.y - o.y) * inv.y;
    tn = fmaxf(tn, fminf(t0, t1)), tf = fminf(tf, fmaxf(t0, t1));
    t0 = (n->lo.z - o.z) * inv.z, t1 = (n->hi.z - o.z) * inv.z;
    tn = fmaxf(tn, fminf(t0, t1)), tf = fminf(tf, fmaxf(t0, t1));
    return tf >= fmaxf(tn, 0) && tn < tmax;
}

/* any = 1: stop at the first hit (shadow rays); hits beyond tmax are misses */
static Hit trace(v3 o, v3 d, float tmin, float tmax, int any)
{
    Hit h = {tmax, 0, 0, -1};
    v3 inv = {1.0f / d.x, 1.0f / d.y, 1.0f / d.z};
    uint32_t stack[64], sp = 0;
    stack[sp++] = 0;
    while (sp) {
        const Node *n = &nodes[stack[--sp]];
        if (!box_hit(n, o, inv, h.t))
            continue;
        if (n->count == 0) {
            if (sp + 2 <= 64)
                stack[sp++] = n->left_or_first, stack[sp++] = n->left_or_first + 1;
            continue;
        }
        for (uint32_t i = 0; i < n->count; i++) {
            uint32_t ti = tidx[n->left_or_first + i];
            v3 a = pos[tri[ti][0]], e1 = sub(pos[tri[ti][1]], a), e2 = sub(pos[tri[ti][2]], a);
            v3 p = cross(d, e2);
            float det = dot(e1, p);
            if (fabsf(det) < 1e-12f)
                continue;
            float id = 1.0f / det;
            v3 s = sub(o, a);
            float u = dot(s, p) * id;
            if (u < 0 || u > 1)
                continue;
            v3 q = cross(s, e1);
            float v = dot(d, q) * id;
            if (v < 0 || u + v > 1)
                continue;
            float t = dot(e2, q) * id;
            if (t > tmin && t < h.t) {
                h = (Hit){t, u, v, (int32_t)ti};
                if (any)
                    return h;
            }
        }
    }
    return h;
}

/* ---- sampling ---- */

static uint32_t pcg(uint64_t *s)
{
    uint64_t old = *s;
    *s = old * 6364136223846793005ULL + 1442695040888963407ULL;
    uint32_t x = (uint32_t)(((old >> 18u) ^ old) >> 27u), r = (uint32_t)(old >> 59u);
    return (x >> r) | (x << ((-r) & 31));
}
static float rnd(uint64_t *s) { return (pcg(s) >> 8) * (1.0f / 16777216.0f); }

static void basis(v3 n, v3 *t, v3 *b)
{
    *t = norm(fabsf(n.x) > 0.5f ? cross(n, (v3){0, 1, 0}) : cross(n, (v3){1, 0, 0}));
    *b = cross(n, *t);
}

static v3 cosine_dir(v3 n, uint64_t *s)
{
    v3 t, b;
    basis(n, &t, &b);
    float r1 = rnd(s), r2 = rnd(s), r = sqrtf(r1), phi = 6.2831853f * r2;
    return norm(add(add(mul(t, r * cosf(phi)), mul(b, r * sinf(phi))), mul(n, sqrtf(fmaxf(0, 1 - r1)))));
}

/* ---- params ---- */

static v3 sun_dir, sun_col, sky_zen, sky_hor, ground_col;
static float sun_cone, eps, ao_radius;
static uint32_t samples, sun_samples, bounces;

typedef struct { v3 p, col; float falloff, size; } Light;
static Light *lights;
static uint32_t nlights, light_samples;

static v3 rand_ball(uint64_t *s)
{
    for (;;) {
        v3 p = {2 * rnd(s) - 1, 2 * rnd(s) - 1, 2 * rnd(s) - 1};
        if (dot(p, p) <= 1)
            return p;
    }
}

static void place_lights(const float *in)
{
    for (uint32_t i = 0; i < nlights; i++, in += 9) {
        v3 base = {in[0], in[1], in[2]}, up = {0, 1, 0};
        Hit h = trace(add(base, mul(up, eps)), up, eps, 1e30f, 0);
        float height = h.tri >= 0 ? fminf(in[3], 0.6f * h.t) : in[3];
        lights[i] = (Light){add(base, mul(up, height)), {in[4], in[5], in[6]}, in[7], in[8]};
    }
}

static v3 sky(v3 d)
{
    if (d.y < 0)
        return ground_col;
    float k = sqrtf(d.y);
    return add(mul(sky_hor, 1 - k), mul(sky_zen, k));
}

static v3 shade_vertex(uint32_t i, const v3 *prev)
{
    v3 n = nrm[i], o = add(pos[i], mul(n, eps)), sum = {0, 0, 0};
    uint64_t s = 0x9E3779B97F4A7C15ULL ^ ((uint64_t)i * 0xD1B54A32D192ED03ULL);
    pcg(&s);

    /* sun: shadow rays through a small cone */
    float ndl = dot(n, sun_dir);
    if (ndl > 0 && sun_samples) {
        v3 t, b;
        basis(sun_dir, &t, &b);
        uint32_t lit = 0;
        for (uint32_t k = 0; k < sun_samples; k++) {
            float r = sun_cone * sqrtf(rnd(&s)), phi = 6.2831853f * rnd(&s);
            v3 d = norm(add(sun_dir, add(mul(t, r * cosf(phi)), mul(b, r * sinf(phi)))));
            lit += trace(o, d, eps, 1e30f, 1).tri < 0;
        }
        sum = mul(sun_col, ndl * lit / sun_samples);
    }

    /* point lights */
    for (uint32_t l = 0; l < nlights; l++) {
        const Light *L = &lights[l];
        v3 to = sub(L->p, o);
        float d2 = dot(to, to), cut = 36 * L->falloff * L->falloff;
        if (d2 >= cut || dot(to, n) <= 0)
            continue;
        float d = sqrtf(d2), w = 1 - d2 / cut;
        float atten = w * w / (1 + d2 / (L->falloff * L->falloff));
        float vis = 0;
        for (uint32_t k = 0; k < light_samples; k++) {
            v3 q = sub(add(L->p, mul(rand_ball(&s), L->size)), o);
            float qd = sqrtf(dot(q, q));
            vis += trace(o, mul(q, 1 / qd), eps, qd * 0.999f, 1).tri < 0;
        }
        sum = add(sum, mul(L->col, dot(n, mul(to, 1 / d)) * atten * vis / light_samples));
    }

    /* sky + bounce: cosine-weighted hemisphere, so each sample weighs 1/N */
    v3 acc = {0, 0, 0};
    for (uint32_t k = 0; k < samples; k++) {
        v3 d = cosine_dir(n, &s);
        Hit h = trace(o, d, eps, ao_radius > 0 ? ao_radius : 1e30f, prev == NULL);
        if (h.tri < 0) {
            acc = add(acc, sky(d));
        } else if (prev) {
            uint32_t *t = tri[h.tri];
            v3 fn = cross(sub(pos[t[1]], pos[t[0]]), sub(pos[t[2]], pos[t[0]]));
            if (dot(fn, d) >= 0) /* back of a face: inside geometry, no light */
                continue;
            float w = 1 - h.u - h.v;
            v3 e = add(add(mul(prev[t[0]], w), mul(prev[t[1]], h.u)), mul(prev[t[2]], h.v));
            v3 a = add(add(mul(alb[t[0]], w), mul(alb[t[1]], h.u)), mul(alb[t[2]], h.v));
            acc = add(acc, vmul(e, a)); /* Lambertian: radiance = albedo * irradiance / pi, pdf cancels pi */
        }
    }
    return add(sum, mul(acc, 1.0f / samples));
}

static void *read_all(FILE *f, size_t n)
{
    void *p = malloc(n ? n : 1);
    if (fread(p, 1, n, f) != n) {
        fprintf(stderr, "rt_bake: short read\n");
        exit(1);
    }
    return p;
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: rt_bake scene.bin irradiance.bin\n");
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) {
        perror(argv[1]);
        return 1;
    }
    uint32_t hdr[3], ip[3];
    float fp[16];
    if (fread(hdr, 4, 3, f) != 3 || hdr[0] != 0x4B425452 || fread(fp, 4, 16, f) != 16 ||
        fread(ip, 4, 3, f) != 3 || fread(&eps, 4, 1, f) != 1 || fread(&ao_radius, 4, 1, f) != 1) {
        fprintf(stderr, "rt_bake: bad header\n");
        return 1;
    }
    nv = hdr[1], nt = hdr[2];
    sun_dir = norm((v3){fp[0], fp[1], fp[2]});
    sun_col = (v3){fp[3], fp[4], fp[5]};
    sun_cone = fp[6];
    sky_zen = (v3){fp[7], fp[8], fp[9]};
    sky_hor = (v3){fp[10], fp[11], fp[12]};
    ground_col = (v3){fp[13], fp[14], fp[15]};
    samples = ip[0], sun_samples = ip[1], bounces = ip[2];
    pos = read_all(f, sizeof(v3) * nv);
    nrm = read_all(f, sizeof(v3) * nv);
    alb = read_all(f, sizeof(v3) * nv);
    tri = read_all(f, 12 * (size_t)nt);
    uint32_t lh[2];
    if (fread(lh, 4, 2, f) != 2) {
        fprintf(stderr, "rt_bake: no light header\n");
        return 1;
    }
    nlights = lh[0], light_samples = lh[1];
    float *light_in = read_all(f, 36 * (size_t)nlights);
    fclose(f);
    for (uint32_t i = 0; i < nt; i++)
        for (int k = 0; k < 3; k++)
            if (tri[i][k] >= nv) {
                fprintf(stderr, "rt_bake: triangle %u index out of range\n", i);
                return 1;
            }

    build_bvh();
    lights = malloc(sizeof(Light) * (nlights ? nlights : 1));
    place_lights(light_in);

    v3 *prev = NULL, *cur = malloc(sizeof(v3) * nv);
    for (uint32_t pass = 0; pass <= bounces; pass++) {
#pragma omp parallel for schedule(dynamic, 64)
        for (uint32_t i = 0; i < nv; i++)
            cur[i] = dot(nrm[i], nrm[i]) > 0 ? shade_vertex(i, prev) : (v3){0, 0, 0};
        if (!prev)
            prev = malloc(sizeof(v3) * nv);
        v3 *t = prev; prev = cur; cur = t;
    }

    f = fopen(argv[2], "wb");
    if (!f || fwrite(prev, sizeof(v3), nv, f) != nv) {
        perror(argv[2]);
        return 1;
    }
    fclose(f);
    fprintf(stderr, "rt_bake: %u verts, %u tris, %u BVH nodes, %u lights, %u bounces\n", nv, nt, nnodes, nlights, bounces);
    return 0;
}
