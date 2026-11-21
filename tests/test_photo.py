import unittest
import os
import shutil

from opendm.photo import ODM_Photo


class TestPhoto(unittest.TestCase):
    def setUp(self):
        pass

    def test_jpeg_xl(self):
        photo = ODM_Photo("tests/assets/test_0001.jxl")
        self.assertEqual(photo.width, 1280)
        self.assertEqual(photo.height, 1024)
        self.assertEqual(photo.camera_make, "Lab 308")
        self.assertEqual(photo.camera_model, "Smart Pilot Cam")
        self.assertEqual(photo.orientation, 1)
        self.assertEqual(photo.utc_time, 1755297953000.0)
        self.assertEqual(photo.latitude, 44.058116008051655)
        self.assertEqual(photo.longitude, -121.31559406946837)
        self.assertEqual(photo.altitude, 2019.845)
        self.assertEqual(photo.yaw, 105.780071)
        self.assertEqual(photo.pitch, 40)
        self.assertEqual(photo.roll, 0)

    def _gps_std(self, xy_stddev, z_stddev, gps_accuracy=None):
        # Return the gps block to_opensfm_exif() builds for the given stored stddev.
        photo = ODM_Photo("tests/assets/test_0001.jxl")
        photo.gps_xy_stddev = xy_stddev
        photo.gps_z_stddev = z_stddev
        return photo.to_opensfm_exif(gps_accuracy=gps_accuracy)["gps"]

    def test_gps_std_per_axis(self):
        # Horizontal and vertical are passed through independently.
        gps = self._gps_std(0.03, 0.05)
        self.assertAlmostEqual(gps["latitude_std"], 0.03)
        self.assertAlmostEqual(gps["longitude_std"], 0.03)
        self.assertAlmostEqual(gps["altitude_std"], 0.05)
        self.assertAlmostEqual(gps["dop"], 0.05)

    def test_gps_std_equal_axes(self):
        # Equal values stay equal, no vertical multiplier.
        gps = self._gps_std(0.05, 0.05)
        self.assertAlmostEqual(gps["latitude_std"], 0.05)
        self.assertAlmostEqual(gps["altitude_std"], 0.05)

    def test_gps_std_missing_vertical(self):
        # Missing vertical defaults to 3x horizontal.
        gps = self._gps_std(0.03, None)
        self.assertAlmostEqual(gps["latitude_std"], 0.03)
        self.assertAlmostEqual(gps["altitude_std"], 0.09)

    def test_gps_std_from_gps_accuracy(self):
        # No stored values: use gps_accuracy, vertical is 3x.
        gps = self._gps_std(None, None, gps_accuracy=2.0)
        self.assertAlmostEqual(gps["latitude_std"], 2.0)
        self.assertAlmostEqual(gps["altitude_std"], 6.0)

    def test_gps_std_default(self):
        # No stored values and no gps_accuracy: default to 10.0, vertical is 3x.
        gps = self._gps_std(None, None)
        self.assertAlmostEqual(gps["latitude_std"], 10.0)
        self.assertAlmostEqual(gps["altitude_std"], 30.0)

    def _camera(self, omega, phi, kappa):
        # OpenSfM's rotation from the world (east, north, up) to its camera (geometry/src/rotation.cc)
        import math
        import numpy as np
        def r(axis, a):
            c, s = math.cos(a), math.sin(a)
            return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
                    "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]
        o, p, k = (math.radians(v) for v in (omega, phi, kappa))
        return np.diag([1.0, -1.0, -1.0]).dot(r("z", -k)).dot(r("y", -p)).dot(r("x", -o))

    def _gimbal(self, yaw, pitch, roll):
        # A DJI photo as parse_exif leaves it: pitch moved so that 0 is nadir
        photo = ODM_Photo("tests/assets/test_0001.jxl")
        photo.gimbal_yaw, photo.pitch, photo.roll = yaw, 90 + pitch, roll
        self.assertTrue(photo.compute_opk_from_gimbal())
        R = self._camera(photo.omega, photo.phi, photo.kappa)
        return R.T.dot([0, 0, 1]), -R.T.dot([0, 1, 0])          # where the camera looks, and where the top of the image points

    def test_gimbal_angles_nadir(self):
        # Mavic 3 Multispectral, spring 2025 mission 15, photo 0168: gimbal yaw -179.7, pitch -89.9, roll 0. It looks down
        # with the top of the image to the south; the solve agreed within 1.7 degrees.
        look, up = self._gimbal(-179.7, -89.9, 0.0)
        self.assertGreater(-look[2], 0.9999)
        self.assertGreater(-up[1], 0.9999)
        # Photo 0164 of the same line: DJI writes the same view as yaw 0.3 with roll 180. Stock ODM turned it upside down.
        look, up = self._gimbal(0.3, -90.0, 180.0)
        self.assertGreater(-look[2], 0.9999)
        self.assertGreater(-up[1], 0.9999)

    def test_gimbal_angles_heading_and_tilt(self):
        look, up = self._gimbal(90.0, -90.0, 0.0)                 # top of the image to the east
        self.assertGreater(up[0], 0.9999)
        look, up = self._gimbal(0.0, -45.0, 0.0)                  # looking north, 45 degrees down
        self.assertAlmostEqual(look[1], 0.7071, places=3)
        self.assertAlmostEqual(look[2], -0.7071, places=3)

    def test_gimbal_angles_need_dji_gimbal_yaw(self):
        photo = ODM_Photo("tests/assets/test_0001.jxl")
        self.assertIsNone(photo.gimbal_yaw)
        self.assertFalse(photo.compute_opk_from_gimbal())

if __name__ == '__main__':
    unittest.main()