from __future__ import absolute_import
import os, shutil, sys, struct, random, math, platform
from opendm.dem import commands
from opendm import system
from opendm import log
from opendm import context
from opendm import concurrency
from opendm import point_cloud
from scipy import signal
import numpy as np

def create_25dmesh(inPointCloud, outMesh, radius_steps=["0.05"], dsm_resolution=0.05, depth=8, samples=1, maxVertexCount=100000, available_cores=None, method='gridded', smooth_dsm=True, max_tiles=None):
    # Create DSM from point cloud

    # Create temporary directory
    mesh_directory = os.path.dirname(outMesh)
    tmp_directory = os.path.join(mesh_directory, 'tmp')
    if os.path.exists(tmp_directory):
        shutil.rmtree(tmp_directory)
    os.mkdir(tmp_directory)
    log.ODM_INFO('Created temporary directory: %s' % tmp_directory)

    log.ODM_INFO('Creating DSM for 2.5D mesh')

    commands.create_dem(
            inPointCloud,
            'mesh_dsm',
            output_type='max',
            radiuses=radius_steps,
            gapfill=True,
            outdir=tmp_directory,
            resolution=dsm_resolution,
            max_workers=available_cores,
            apply_smoothing=smooth_dsm,
            max_tiles=max_tiles
        )

    if method == 'gridded':
        mesh = dem_to_mesh_gridded(os.path.join(tmp_directory, 'mesh_dsm.tif'), outMesh, maxVertexCount, maxConcurrency=max(1, available_cores))
    elif method == 'poisson':
        dsm_points = dem_to_points(os.path.join(tmp_directory, 'mesh_dsm.tif'), os.path.join(tmp_directory, 'dsm_points.ply'))
        mesh = screened_poisson_reconstruction(dsm_points, outMesh, depth=depth, 
                                    samples=samples, 
                                    maxVertexCount=maxVertexCount, 
                                    threads=max(1, available_cores - 1)), # poissonrecon can get stuck on some machines if --threads == all cores
    else:
        raise 'Not a valid method: ' + method

    # Cleanup tmp
    if os.path.exists(tmp_directory):
        shutil.rmtree(tmp_directory)

    return mesh


def dem_to_points(inGeotiff, outPointCloud):
    log.ODM_INFO('Sampling points from DSM: %s' % inGeotiff)

    kwargs = {
        'bin': context.dem2points_path,
        'outfile': outPointCloud,
        'infile': inGeotiff
    }

    system.run('"{bin}" -inputFile "{infile}" '
         '-outputFile "{outfile}" '
         '-skirtHeightThreshold 1.5 '
         '-skirtIncrements 0.2 '
         '-skirtHeightCap 100 '
         '-verbose '.format(**kwargs))

    return outPointCloud


def dem_to_mesh_gridded(inGeotiff, outMesh, maxVertexCount, maxConcurrency=1):
    log.ODM_INFO('Creating mesh from DSM: %s' % inGeotiff)

    mesh_path, mesh_filename = os.path.split(outMesh)
    # mesh_path = path/to
    # mesh_filename = odm_mesh.ply

    basename, ext = os.path.splitext(mesh_filename)
    # basename = odm_mesh
    # ext = .ply

    outMeshDirty = os.path.join(mesh_path, "{}.dirty{}".format(basename, ext))

    # This should work without issues most of the times, 
    # but just in case we lower maxConcurrency if it fails.
    while True:
        try:
            kwargs = {
                'bin': context.dem2mesh_path,
                'outfile': outMeshDirty,
                'infile': inGeotiff,
                'maxVertexCount': maxVertexCount,
                'maxConcurrency': maxConcurrency
            }
            system.run('"{bin}" -inputFile "{infile}" '
                '-outputFile "{outfile}" '
                '-maxTileLength 2000 '
                '-maxVertexCount {maxVertexCount} '
                '-maxConcurrency {maxConcurrency} '
                '-edgeSwapThreshold 0.15 '
                '-verbose '.format(**kwargs))
            break
        except Exception as e:
            maxConcurrency = math.floor(maxConcurrency / 2)
            if maxConcurrency >= 1:
                log.ODM_WARNING("dem2mesh failed, retrying with lower concurrency (%s) in case this is a memory issue" % maxConcurrency)
            else:
                raise e


    # Cleanup and reduce vertex count if necessary 
    # (as dem2mesh cannot guarantee that we'll have the target vertex count)
    cleanupArgs = {
        'reconstructmesh': context.omvs_reconstructmesh_path,
        'outfile': outMesh,
        'infile': outMeshDirty,
        'max_faces': maxVertexCount * 2
    }

    system.run('"{reconstructmesh}" -i "{infile}" '
         '-o "{outfile}" '
         '--archive-type 3 '
         '--remove-spikes 0 --remove-spurious 0 --smooth 0 '
         '--target-face-num {max_faces} -v 0'.format(**cleanupArgs))

    # Delete intermediate results
    os.remove(outMeshDirty)

    # The cleanup leaves a few small openings in the surface; close them so texturing has a face to color there.
    # The mesh file is only replaced at the very end, so a failure here leaves the cleaned mesh as it was
    try:
        close_small_openings(outMesh)
    except Exception as e:
        log.ODM_WARNING("Could not close small openings in the 2.5D mesh (%s); it is used as the cleanup left it" % str(e))

    return outMesh


def close_small_openings(mesh_file, max_edges=8):
    """Close the small openings ReconstructMesh's cleanup leaves in the 2.5D mesh.

    The cleanup removes faces where the surface touches itself and closes the holes that opens, but its hole closing
    gives up on some of them. A few dozen openings of three or four edges stay in a mission's mesh (58 in the mesh
    of fall 2025 mission 07 made again from its point cloud; dem2mesh's own output had none). Nothing is drawn
    there, so each shows as a triangle of no data in the orthophoto (mosscoder/mpg-aerial-pipeline issue 68).

    Every opening of up to max_edges edges gets triangles between its own corners. No vertex is added or moved, and
    the file keeps its layout, with the new faces at the end. Texturing then colors the new faces from the photos
    like any other face. Left alone: larger openings, the outer edge, pieces of surface that stand on their own,
    openings pinched at a corner, and openings whose corners lie on one line.

    Returns (openings closed, triangles added, openings left), or None when the file is not the binary PLY of
    x, y, z vertices and triangles that ReconstructMesh writes.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    face_dtype = np.dtype([('n', 'u1'), ('i', '<u4', 3)])
    with open(mesh_file, 'rb') as f:
        header = b''
        while not header.endswith(b'end_header\n'):
            line = f.readline()
            if not line or len(header) > 4096:
                log.ODM_WARNING("Cannot close small openings: no PLY header in %s" % mesh_file)
                return None
            header += line
        lines = [l.strip() for l in header.decode('ascii', 'replace').split('\n') if l.strip()]
        props = [l.split() for l in lines if l.startswith('property')]
        elements = [l.split() for l in lines if l.startswith('element')]
        expected = (lines[:2] == ['ply', 'format binary_little_endian 1.0']
                    and [e[1] for e in elements] == ['vertex', 'face']
                    and [p[1:] for p in props[:3]] in ([['float', 'x'], ['float', 'y'], ['float', 'z']],
                                                       [['float32', 'x'], ['float32', 'y'], ['float32', 'z']])
                    and len(props) == 4 and props[3][1] == 'list' and props[3][2] in ('uchar', 'uint8')
                    and props[3][3] in ('uint', 'uint32', 'int', 'int32'))
        if not expected:
            log.ODM_WARNING("Cannot close small openings: unexpected PLY layout in %s" % mesh_file)
            return None
        nv, nf = int(elements[0][2]), int(elements[1][2])
        vertex_bytes = f.read(nv * 12)
        faces = np.frombuffer(f.read(nf * face_dtype.itemsize), dtype=face_dtype)
    if len(vertex_bytes) != nv * 12 or len(faces) != nf or nf == 0 or not (faces['n'] == 3).all():
        log.ODM_WARNING("Cannot close small openings: %s is not a triangle mesh" % mesh_file)
        return None
    V = np.frombuffer(vertex_bytes, dtype='<f4').reshape(nv, 3).astype(np.float64)
    F = faces['i'].astype(np.int64)

    # Border edges: the edges on exactly one face, each in the direction its face runs it
    a, b = F.ravel(), F[:, [1, 2, 0]].ravel()
    key = np.minimum(a, b) * nv + np.maximum(a, b)
    order = np.argsort(key, kind='stable')
    ks = key[order]
    start = np.r_[True, ks[1:] != ks[:-1]]
    edge_keys = ks[start]                                          # every edge of the mesh, sorted
    border = order[start & np.r_[ks[1:] != ks[:-1], True]]
    if len(border) == 0:
        return (0, 0, 0)
    ba, bb, bf = a[border], b[border], border // 3

    def is_edge(u, v):
        k = min(u, v) * nv + max(u, v)
        i = np.searchsorted(edge_keys, k)
        return i < len(edge_keys) and edge_keys[i] == k

    # Groups of border edges that hang together; the outer edge of the mesh is one large group
    corners, compact = np.unique(np.r_[ba, bb], return_inverse=True)
    ca, cb = compact[:len(ba)], compact[len(ba):]
    _, group = connected_components(coo_matrix((np.ones(len(ca), np.int8), (ca, cb)), shape=(len(corners),) * 2), directed=False)
    by_group = {}
    for e, g in enumerate(group[ca]):
        by_group.setdefault(g, []).append(e)

    new_faces = []
    closed = left = 0
    for edges in by_group.values():
        n = len(edges)
        if n > max_edges:
            continue
        nxt, links = {}, {}
        for e in edges:
            u, v = int(ba[e]), int(bb[e])
            nxt.setdefault(u, []).append(v)
            links.setdefault(u, []).append(v)
            links.setdefault(v, []).append(u)
        loop = set(links)
        # an opening: one ring of n corners, and every face along it reaches a vertex outside the ring
        # (a piece of surface standing on its own has all its vertices on its own border)
        if n < 3 or len(loop) != n or any(len(l) != 2 for l in links.values()) \
                or any(len(loop.intersection(F[bf[e]].tolist())) != 2 for e in edges):
            left += 1
            continue
        ring = [int(ba[edges[0]])]
        while len(ring) < n:
            u = ring[-1]
            ring.append(links[u][0] if len(ring) == 1 or links[u][0] != ring[-2] else links[u][1])
        # the faces around the opening run its edges one way round when they are wound alike; the new faces then
        # run them the other way. Where they are not wound alike, each new face is turned to face up
        alike = all(len(nxt.get(u, [])) == 1 for u in loop)
        if alike:
            ring = [ring[0]]
            while len(ring) < n:
                ring.append(nxt[ring[-1]][0])
            ring.reverse()
        best = None
        for i in range(n):                                           # a fan of triangles from corner i
            fan = [(ring[i], ring[(i + j) % n], ring[(i + j + 1) % n]) for j in range(1, n - 1)]
            diagonals = [(ring[i], ring[(i + j) % n]) for j in range(2, n - 1)]
            normals = [np.cross(V[t[1]] - V[t[0]], V[t[2]] - V[t[0]]) for t in fan]
            # flat: thinner than a tenth of a millimeter (the cross product's length is the longest side times that)
            longest = [max(np.linalg.norm(V[t[k]] - V[t[k - 1]]) for k in range(3)) for t in fan]
            flat = [np.linalg.norm(nrm) < 1e-4 * side for nrm, side in zip(normals, longest)]
            cost = (sum(is_edge(u, v) for u, v in diagonals), sum(flat), sum(np.linalg.norm(V[u] - V[v]) for u, v in diagonals))
            if best is None or cost < best[0]:
                best = (cost, fan, normals, flat)
        cost, fan, normals, flat = best
        if cost[0] > 0 or all(flat):
            left += 1                                                # would share an edge with other faces, or flat
            continue
        for t, nrm, is_flat in zip(fan, normals, flat):
            if not is_flat:
                new_faces.append(t if alike or nrm[2] >= 0 else (t[0], t[2], t[1]))
        closed += 1

    if new_faces:
        added = np.zeros(len(new_faces), dtype=face_dtype)
        added['n'] = 3
        added['i'] = np.array(new_faces, dtype=np.int64)
        tmp_file = mesh_file + '.closing'
        with open(tmp_file, 'wb') as f:
            f.write(header.replace(('element face %d' % nf).encode(), ('element face %d' % (nf + len(new_faces))).encode(), 1))
            f.write(vertex_bytes)
            f.write(faces.tobytes())
            f.write(added.tobytes())
        os.replace(tmp_file, mesh_file)
    log.ODM_INFO("Closed %s small openings in the 2.5D mesh with %s triangles (%s left as they are)" % (closed, len(new_faces), left))
    return (closed, len(new_faces), left)


def screened_poisson_reconstruction(inPointCloud, outMesh, depth = 8, samples = 1, maxVertexCount=100000, pointWeight=4, threads=context.num_cores):

    mesh_path, mesh_filename = os.path.split(outMesh)
    # mesh_path = path/to
    # mesh_filename = odm_mesh.ply

    basename, ext = os.path.splitext(mesh_filename)
    # basename = odm_mesh
    # ext = .ply

    outMeshDirty = os.path.join(mesh_path, "{}.dirty{}".format(basename, ext))
    if os.path.isfile(outMeshDirty):
        os.remove(outMeshDirty)

    # Keep PoissonRecon scratch files in a writable, mesh-specific directory.
    tempdir = os.path.abspath(os.path.join(mesh_path, "%s-tmp" % basename))
    if os.path.exists(tempdir):
        log.ODM_WARNING("Removing previous PoissonRecon temp directory: %s" % tempdir)
        shutil.rmtree(tempdir)
    os.makedirs(tempdir)
    
    # Since PoissonRecon has some kind of a race condition on ppc64el, and this helps...
    if platform.machine() == 'ppc64le':
        log.ODM_WARNING("ppc64le platform detected, forcing single-threaded operation for PoissonRecon")
        threads = 1

    while True:
        poissonReconArgs = {
            'bin': context.poisson_recon_path,
            'outfile': outMeshDirty,
            'infile': inPointCloud,
            'tempdir': tempdir,
            'depth': depth,
            'samples': samples,
            'pointWeight': pointWeight,
            'threads': int(threads)
        }

        # Run PoissonRecon
        try:
            system.run('"{bin}" --in "{infile}" '
                    '--out "{outfile}" '
                    '--tempDir "{tempdir}" '
                    '--depth {depth} '
                    '--pointWeight {pointWeight} '
                    '--samplesPerNode {samples} '
                    '--threads {threads} '
                    '--bType 2 '
                    '--linearFit '.format(**poissonReconArgs))
        except Exception as e:
            log.ODM_WARNING(str(e))
            
        if os.path.isfile(outMeshDirty):
            break # Done!
        else:

            # PoissonRecon will sometimes fail due to race conditions
            # on certain machines, especially on Windows
            threads //= 2

            if threads < 1:
                break
            else:
                log.ODM_WARNING("PoissonRecon failed with %s threads, let's retry with %s..." % (threads * 2, threads))


    if os.path.exists(tempdir):
        shutil.rmtree(tempdir)

    # Cleanup and reduce vertex count if necessary
    cleanupArgs = {
        'reconstructmesh': context.omvs_reconstructmesh_path,
        'outfile': outMesh,
        'infile':outMeshDirty,
        'max_faces': maxVertexCount * 2
    }

    system.run('"{reconstructmesh}" -i "{infile}" '
         '-o "{outfile}" '
         '--archive-type 3 '
         '--remove-spikes 0 --remove-spurious 20 --smooth 0 '
         '--target-face-num {max_faces} -v 0'.format(**cleanupArgs))

    # Delete intermediate results
    os.remove(outMeshDirty)

    return outMesh
