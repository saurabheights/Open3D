# ----------------------------------------------------------------------------
# -                        Open3D: www.open3d.org                            -
# ----------------------------------------------------------------------------
# The MIT License (MIT)
#
# Copyright (c) 2018-2021 www.open3d.org
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
# ----------------------------------------------------------------------------

from tqdm import tqdm
import numpy as np
import open3d as o3d
import open3d.core as o3c
from config import ConfigParser
from common import load_rgbd_file_names, load_depth_file_names, save_poses, load_intrinsic, load_extrinsics, get_default_dataset


def rgbd_loop_closure(depth_list, color_list, intrinsic, config):
    device = o3c.Device(config.device)

    interval = config.odometry_loop_interval
    n_files = len(depth_list)

    key_indices = list(range(0, n_files, interval))
    n_key_indices = len(key_indices)

    edges = []
    poses = []
    infos = []

    pairs = []

    criteria_list = [
        o3d.t.pipelines.odometry.OdometryConvergenceCriteria(20),
        o3d.t.pipelines.odometry.OdometryConvergenceCriteria(10),
        o3d.t.pipelines.odometry.OdometryConvergenceCriteria(5)
    ]
    method = o3d.t.pipelines.odometry.Method.PointToPlane

    # Running loop closure between all the keyframes.
    for i in range(n_key_indices - 1):
        key_i = key_indices[i]
        depth_curr = o3d.t.io.read_image(depth_list[key_i]).to(device)
        color_curr = o3d.t.io.read_image(color_list[key_i]).to(device)
        rgbd_curr = o3d.t.geometry.RGBDImage(color_curr, depth_curr)

        for j in range(i + 1, n_key_indices):
            key_j = key_indices[j]
            depth_next = o3d.t.io.read_image(depth_list[key_j]).to(device)
            color_next = o3d.t.io.read_image(color_list[key_j]).to(device)
            rgbd_next = o3d.t.geometry.RGBDImage(color_next, depth_next)

            # TODO: add OpenCV initialization if necessary.
            # TODO: better failure check
            try:
                res = o3d.t.pipelines.odometry.rgbd_odometry_multi_scale(
                    rgbd_curr, rgbd_next, intrinsic, o3c.Tensor(np.eye(4)),
                    config.depth_scale, config.depth_max, criteria_list, method)
                info = o3d.t.pipelines.odometry.compute_odometry_information_matrix(
                    depth_curr, depth_next, intrinsic, res.transformation,
                    config.odometry_distance_thr, config.depth_scale,
                    config.depth_max)
            except Exception as e:
                pass
            else:
                if info[5, 5] / (depth_curr.columns * depth_curr.rows) > 0.3:
                    edges.append((key_i, key_j))
                    poses.append(res.transformation.cpu().numpy())
                    infos.append(info.cpu().numpy())

                if (config.debug_mode):
                    print("[DEBUG] Loop closure point-cloud allignment between "
                          "fragment {} and fragment {}".format(key_i, key_j))

                    pcd_src = o3d.t.geometry.PointCloud.create_from_rgbd_image(
                        rgbd_curr, intrinsic)
                    pcd_dst = o3d.t.geometry.PointCloud.create_from_rgbd_image(
                        rgbd_next, intrinsic)
                    o3d.visualization.draw(
                        [pcd_src, pcd_dst],
                        title="Loop Closure Fragment {}, {} Aligment Input".
                        format(key_i, key_j))
                    o3d.visualization.draw(
                        [pcd_src.transform(res.transformation), pcd_dst],
                        title="Loop Closure Fragment {}, {} Aligment Result".
                        format(key_i, key_j))

    return edges, poses, infos


def rgbd_odometry(depth_list, color_list, intrinsic, config):
    device = o3c.Device(config.device)

    # Load input rgb-d image.
    depth_curr = o3d.t.io.read_image(depth_list[0]).to(device)
    color_curr = o3d.t.io.read_image(color_list[0]).to(device)
    rgbd_curr = o3d.t.geometry.RGBDImage(color_curr, depth_curr)

    # Set odometry convergence criteria and method.
    criteria_list = [
        o3d.t.pipelines.odometry.OdometryConvergenceCriteria(20),
        o3d.t.pipelines.odometry.OdometryConvergenceCriteria(10),
        o3d.t.pipelines.odometry.OdometryConvergenceCriteria(5)
    ]

    method = o3d.t.pipelines.odometry.Method
    if (config.odometry_method == 'point2plane'):
        method = o3d.t.pipelines.odometry.Method.PointToPlane
    elif (config.odometry_method == 'intensity'):
        method = o3d.t.pipelines.odometry.Method.Intensity
    elif (config.odometry_method == 'hybrid'):
        method = o3d.t.pipelines.odometry.Method.Hybrid
    else:
        raise Exception('odometry method: {} is not implemented.'.format(
            config.odometry_method))

    # Compute pose graph from frame to frame odometry.
    edges = []
    poses = []
    infos = []
    for i in tqdm(range(0, len(depth_list) - 1)):
        depth_next = o3d.t.io.read_image(depth_list[i + 1]).to(device)
        color_next = o3d.t.io.read_image(color_list[i + 1]).to(device)
        rgbd_next = o3d.t.geometry.RGBDImage(color_next, depth_next)

        res = o3d.t.pipelines.odometry.rgbd_odometry_multi_scale(
            rgbd_curr, rgbd_next, intrinsic, o3c.Tensor(np.eye(4)),
            config.depth_scale, config.depth_max, criteria_list, method)
        info = o3d.t.pipelines.odometry.compute_odometry_information_matrix(
            depth_curr, depth_next, intrinsic, res.transformation,
            config.odometry_distance_thr, config.depth_scale, config.depth_max)

        edges.append((i, i + 1))
        poses.append(res.transformation.cpu().numpy())
        infos.append(info.cpu().numpy())

        color_curr = color_next
        depth_curr = depth_next
        rgbd_curr = rgbd_next

    return edges, poses, infos