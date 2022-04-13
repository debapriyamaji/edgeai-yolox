import torch
import numpy as np
from os import path
from plyfile import PlyData
from loguru import logger
from math import cos, sin
from .dist import get_local_rank
import cv2

#Order is same as https://github.com/ybkscht/EfficientPose/blob/main/generators/occlusion.py since we use the same dataset

r_h = 640 / 640
r_w = 640 / 640

px = 325.26110
py = 242.04899

fx = 572.41140
fy = 573.57043

def calculate_model_rotation(point_cloud, rvec):
    #rvec = rvec.cpu()
    point_cloud = point_cloud.to(device="cuda:{}".format(get_local_rank()))
    theta = float(rvec.norm(dim = 0))
    if theta != 0:
        k = rvec / theta
    else:
        k = 0 * rvec
    rows = int(point_cloud.shape[0])
    k_cross = torch.tensor([]).to(device="cuda:{}".format(get_local_rank()))
    for _ in range(rows):
        k_cross = torch.cat((k_cross, k))
    k_cross = k_cross.reshape(rows, 3)
    k = k.reshape(1, 3)

    points_transformed = point_cloud * cos(theta) + torch.cross(k_cross, point_cloud) * sin(theta) + k_cross * torch.mm(point_cloud, k.transpose(0, 1)) * (1 - cos(theta))
    return points_transformed


def decode_rotation_translation(pose):
    """
        Args:
            pose : Pose predicted by the model or ground truth pose

        Returns:
            Rotation matrix
            Translatation vector
    """
    # Rotation matrix is recovered using the formula given in the article
    # https://towardsdatascience.com/better-rotation-representations-for-accurate-pose-estimation-e890a7e1317f
    if torch.is_tensor(pose):
        pose = pose.cpu().numpy()

    r1 = np.expand_dims(pose[5:8].astype(np.float64), axis=1)
    r2 = np.expand_dims(pose[8:11].astype(np.float64), axis=1)
    r3 = np.cross(r1.T, r2.T).T
    translation_vec = pose[11:14].astype(np.float64)
    # Tz was previously scaled down by 100 (converted from cm to m)
    # Tx and Ty are recovered using the formula given on page 5 of the the paper: https://arxiv.org/pdf/2011.04307.pdf
    # px, py, fx and fy are currently hard-coded for LINEMOD dataset
    tz = pose[13].astype(np.float64) * 100.0
    # print("prediction",obj_class, tz)
    tx = ((pose[11].astype(np.float64) / r_w) - px) * tz / fx
    ty = ((pose[12].astype(np.float64) / r_h) - py) * tz / fy
    rotation_mat = np.hstack((r1, r2, r3))
    rotation_vec, _ = cv2.Rodrigues(rotation_mat)
    translation_vec[0] = tx
    translation_vec[1] = ty
    translation_vec[2] = tz
    return rotation_vec, translation_vec

def load_models(models_datapath, class_to_name=None):
    class_to_model = {class_id: None for class_id in class_to_name.keys()}
    logger.info("Loading 3D models...")

    for class_id, name in class_to_name.items():
        file = "obj_{:02}.ply".format(class_id + 1)
        model_datapath = path.join(models_datapath, file)

        if not path.isfile(model_datapath):
            logger.warning(
                "The file {} model for class {} was not found".format(file, name)
            )
            continue

        model_3D = load_model_point_cloud(model_datapath)
        class_to_model[class_id] = torch.tensor(model_3D, requires_grad=False).half()

    return class_to_model 

def load_model_point_cloud(datapath):
    model = PlyData.read(datapath)
                                  
    vertex = model['vertex']
    points = np.stack([vertex[:]['x'], vertex[:]['y'], vertex[:]['z']], axis = -1).astype(np.float64)
        
    return points