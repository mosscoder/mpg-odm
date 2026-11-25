import os
import shutil
import tempfile
import unittest

import numpy as np

from opendm import mesh

FACE = np.dtype([('n', 'u1'), ('i', '<u4', 3)])


def grid(n=7):
    """A gently sloping n x n grid of vertices and its triangles, all wound to face up."""
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float64)
    V = np.column_stack((xs.ravel(), ys.ravel(), 0.1 * xs.ravel() + 0.05 * ys.ravel()))
    F = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b, d, e = r * n + c, r * n + c + 1, (r + 1) * n + c, (r + 1) * n + c + 1
            F += [(a, b, e), (a, e, d)]
    return V, np.array(F)


def write_ply(path, V, F, vertex_type='float32'):
    faces = np.zeros(len(F), dtype=FACE)
    faces['n'] = 3
    faces['i'] = F
    with open(path, 'wb') as f:
        f.write(('ply\nformat binary_little_endian 1.0\nelement vertex %d\nproperty %s x\nproperty %s y\nproperty %s z\n'
                 'element face %d\nproperty list uint8 uint32 vertex_indices\nend_header\n'
                 % (len(V), vertex_type, vertex_type, vertex_type, len(F))).encode())
        f.write(np.asarray(V, dtype='<f4').tobytes())
        f.write(faces.tobytes())


def read_ply(path):
    with open(path, 'rb') as f:
        header = b''
        while not header.endswith(b'end_header\n'):
            header += f.readline()
        lines = header.decode().split('\n')
        nv = int([l for l in lines if l.startswith('element vertex')][0].split()[-1])
        nf = int([l for l in lines if l.startswith('element face')][0].split()[-1])
        V = np.frombuffer(f.read(nv * 12), dtype='<f4').reshape(nv, 3)
        faces = np.frombuffer(f.read(), dtype=FACE)
    assert len(faces) == nf
    return header, V, faces['i'].astype(np.int64)


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def border_edges(F):
    edges = {}
    for t in F:
        for u, v in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edges.setdefault((min(u, v), max(u, v)), []).append((u, v))
    return edges, sorted(k for k, uses in edges.items() if len(uses) == 1)


def normal_z(V, t):
    n = np.cross(V[t[1]] - V[t[0]], V[t[2]] - V[t[0]])
    return n[2] / np.linalg.norm(n)


class TestCloseSmallOpenings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'mesh.ply')

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_triangle_and_quad_openings_are_closed(self):
        V, F = grid()
        _, outer = border_edges(F)
        # one triangle out near one corner, the two triangles of one cell out near the other
        missing = [2 * (1 * 6 + 1), 2 * (4 * 6 + 3), 2 * (4 * 6 + 3) + 1]
        kept = np.delete(F, missing, axis=0)
        # and a triangle standing on its own, which is not an opening
        V2 = np.vstack((V, [[20, 20, 0], [21, 20, 0], [20, 21, 0]]))
        kept = np.vstack((kept, [[len(V), len(V) + 1, len(V) + 2]]))
        write_ply(self.path, V2, kept)
        before = read_bytes(self.path)

        self.assertEqual(mesh.close_small_openings(self.path), (2, 3, 1))

        header, V3, F3 = read_ply(self.path)
        after = read_bytes(self.path)
        self.assertIn(b'element face %d\n' % (len(kept) + 3), header)
        self.assertEqual(len(F3), len(kept) + 3)
        self.assertTrue(np.array_equal(V3, V2.astype('<f4')))
        self.assertTrue(np.array_equal(F3[:len(kept)], kept))
        # the bytes of the vertices and of the old faces are the ones that were there
        head_len = before.index(b'end_header\n') + len(b'end_header\n')
        self.assertEqual(after[after.index(b'end_header\n') + len(b'end_header\n'):][:len(before) - head_len], before[head_len:])
        # only the outer edge and the lone triangle have border edges now, and no edge has more than two faces
        edges, border = border_edges(F3)
        lone = [(len(V), len(V) + 1), (len(V), len(V) + 2), (len(V) + 1, len(V) + 2)]
        self.assertEqual(border, sorted(outer + lone))
        self.assertTrue(all(len(uses) <= 2 for uses in edges.values()))
        # each shared edge is run once each way, so the new faces are wound like their neighbors, and they face up
        self.assertTrue(all(len(set(uses)) == len(uses) for uses in edges.values()))
        for t in F3[len(kept):]:
            self.assertGreater(normal_z(V3.astype(np.float64), t), 0.9)
        # the triangle that was taken out is back, and the cell is closed by two triangles between its four corners
        added = [tuple(sorted(int(v) for v in t)) for t in F3[len(kept):]]
        triangle = tuple(sorted(int(v) for v in F[missing[0]]))
        self.assertIn(triangle, added)
        added.remove(triangle)
        cell = set(int(v) for v in F[missing[1:]].ravel())
        self.assertEqual(len(cell), 4)
        self.assertEqual(set(added[0]) | set(added[1]), cell)

        # a second call finds nothing to close
        self.assertEqual(mesh.close_small_openings(self.path), (0, 0, 1))
        self.assertEqual(read_bytes(self.path), after)

    def test_new_face_faces_up_where_neighbors_are_wound_differently(self):
        V, F = grid()
        gone = 2 * (2 * 6 + 2)
        a, b, e = F[gone]
        F = F.copy()
        # turn over the neighbor across one edge of the opening, as a pinched surface has it
        neighbor = [i for i, t in enumerate(F) if i != gone and a in t and b in t][0]
        F[neighbor] = F[neighbor][[0, 2, 1]]
        kept = np.delete(F, gone, axis=0)
        write_ply(self.path, V, kept, vertex_type='float')

        self.assertEqual(mesh.close_small_openings(self.path), (1, 1, 0))
        _, V3, F3 = read_ply(self.path)
        self.assertEqual(tuple(sorted(F3[-1])), tuple(sorted((a, b, e))))
        self.assertGreater(normal_z(V3.astype(np.float64), F3[-1]), 0.9)

    def test_large_openings_and_flat_ones_stay(self):
        V, F = grid(9)
        # a 2 x 3 block of cells out: an opening of 10 edges, more than the 8 that are closed
        missing = [2 * (r * 8 + c) + k for r in (3, 4) for c in (2, 3, 4) for k in (0, 1)]
        write_ply(self.path, V, np.delete(F, missing, axis=0))
        before = read_bytes(self.path)
        self.assertEqual(mesh.close_small_openings(self.path), (0, 0, 0))
        self.assertEqual(read_bytes(self.path), before)

        # an opening whose three corners lie on one line has nothing to draw
        V, F = grid()
        gone = 2 * (2 * 6 + 2)
        a, b, e = F[gone]
        V[e] = (V[a] + V[b]) / 2
        write_ply(self.path, V, np.delete(F, gone, axis=0))
        self.assertEqual(mesh.close_small_openings(self.path), (0, 0, 1))

    def test_other_files_are_left_alone(self):
        V, F = grid()
        kept = np.delete(F, [14], axis=0)
        with open(self.path, 'w') as f:
            f.write('ply\nformat ascii 1.0\nelement vertex %d\nproperty float x\nproperty float y\nproperty float z\n'
                    'element face %d\nproperty list uchar uint vertex_indices\nend_header\n' % (len(V), len(kept)))
            for p in V:
                f.write('%f %f %f\n' % tuple(p))
            for t in kept:
                f.write('3 %d %d %d\n' % tuple(t))
        before = read_bytes(self.path)
        self.assertIsNone(mesh.close_small_openings(self.path))
        self.assertEqual(read_bytes(self.path), before)


if __name__ == '__main__':
    unittest.main()
