"""Match feature descriptors between photo pairs on the GPU, in place of OpenSfM's FLANN search.

Runs in an environment with PyTorch (MPG_TORCH_PYTHON), separate from ODM's. OpenSfM writes the pair list,
this script finds matches, and OpenSfM reads them back and runs its usual geometry check. Works on MPS,
CUDA and CPU. All numbers are 32-bit, which is all MPS offers.

The rule is the same as OpenSfM's symmetric FLANN matching. A feature in photo 1 and one in photo 2 match
when each is the other's nearest neighbor, and each passes the ratio test: nearest squared distance below
ratio squared times second-nearest squared distance. The search here is exact, while FLANN is approximate.

Usage: torch_match.py <opensfm_dir> <pairs.json> <out.npz> [--device auto|mps|cuda|cpu] [--ratio 0.85]
                      [--cache-photos 512] [--limit-pairs N]
"""
import argparse
import collections
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch


def pick_device(name):
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_descriptors(opensfm_dir, image):
    with np.load(os.path.join(opensfm_dir, "features", f"{image}.features.npz"), allow_pickle=False) as z:
        return z["descriptors"].astype(np.float32)


class PhotoCache:
    """Keeps recently used descriptors on the device, and reads upcoming ones in the background."""

    def __init__(self, opensfm_dir, device, size):
        self.dir, self.device, self.size = opensfm_dir, device, size
        self.on_device = collections.OrderedDict()
        self.pending = {}
        self.pool = ThreadPoolExecutor(4)

    def prefetch(self, images):
        for image in images:
            if image not in self.on_device and image not in self.pending:
                self.pending[image] = self.pool.submit(load_descriptors, self.dir, image)

    def get(self, image):
        if image in self.on_device:
            self.on_device.move_to_end(image)
            return self.on_device[image]
        future = self.pending.pop(image, None)
        array = future.result() if future else load_descriptors(self.dir, image)
        tensor = torch.from_numpy(array).to(self.device)
        self.on_device[image] = tensor
        while len(self.on_device) > self.size:
            self.on_device.popitem(last=False)
        return tensor


def two_smallest(d, dim):
    """The smallest value, its index, and the second smallest value along one dimension."""
    first, index = d.min(dim=dim)
    d.scatter_(dim, index.unsqueeze(dim), float("inf"))
    second = d.min(dim=dim).values
    d.scatter_(dim, index.unsqueeze(dim), first.unsqueeze(dim))
    return first, index, second


def match_pair(d1, d2, squared_ratio):
    """Returns an (n, 2) tensor of matched feature indexes, photo 1 then photo 2."""
    # Squared distances from all of photo 1 to all of photo 2.
    d = (d1 * d1).sum(1, keepdim=True) + (d2 * d2).sum(1) - 2 * d1 @ d2.T
    first12, near12, second12 = two_smallest(d, 1)
    first21, near21, second21 = two_smallest(d, 0)
    good12 = first12 < squared_ratio * second12
    good21 = first21 < squared_ratio * second21
    index1 = torch.arange(d1.shape[0], device=d1.device)
    mutual = near21[near12] == index1
    keep = good12 & mutual & good21[near12]
    return torch.stack([index1[keep], near12[keep]], dim=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("opensfm_dir")
    ap.add_argument("pairs_json")
    ap.add_argument("out_npz")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--ratio", type=float, default=0.85)
    ap.add_argument("--cache-photos", type=int, default=512)
    ap.add_argument("--limit-pairs", type=int, default=0)
    args = ap.parse_args()

    device = pick_device(args.device)
    pairs = json.load(open(args.pairs_json))
    if args.limit_pairs:
        pairs = pairs[:args.limit_pairs]
    squared_ratio = args.ratio ** 2
    # Pairs sharing a first photo stay together, so its descriptors stay on the device.
    order = sorted(range(len(pairs)), key=lambda k: pairs[k])
    cache = PhotoCache(args.opensfm_dir, device, args.cache_photos)
    results = [None] * len(pairs)
    t0 = time.time()
    with torch.inference_mode():
        for n, k in enumerate(order):
            cache.prefetch([x for j in order[n + 1:n + 9] for x in pairs[j]])
            im1, im2 = pairs[k]
            results[k] = match_pair(cache.get(im1), cache.get(im2), squared_ratio).cpu().numpy().astype(np.int32)
            if (n + 1) % 500 == 0:
                print(f"  {n + 1}/{len(pairs)} pairs, {time.time() - t0:.0f} s", flush=True)
    elapsed = time.time() - t0
    counts = np.array([len(r) for r in results], dtype=np.int64)
    np.savez(args.out_npz, counts=counts, matches=np.concatenate(results) if results else np.zeros((0, 2), np.int32))
    print(f"matched {len(pairs)} pairs on {device} in {elapsed:.1f} s ({1000 * elapsed / max(1, len(pairs)):.1f} ms per pair); "
          f"median {int(np.median(counts)) if len(counts) else 0} matches per pair", flush=True)


if __name__ == "__main__":
    main()
