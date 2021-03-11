# import midaGAN.nn.losses.ssim as ssim
import numpy as np
from typing import Optional

import numpy as np
from skimage.metrics import peak_signal_noise_ratio, structural_similarity


def get_npy(input):
    """
    Gets numpy array from torch tensor after squeeze op.
    If a mask is provided, a masked array is created.
    """
    input = input.detach().cpu().numpy()
    return input


def create_masked_array(input, mask):
    """
    Create a masked array after applying the respective mask. 
    This mask array will filter values across different operations such as mean
    """
    mask = mask.detach().cpu().numpy()

    mask = mask.astype(np.bool)
    # Masked array needs negated masks as it decides
    # what element to ignore based on True values
    negated_mask = ~mask
    return np.ma.masked_array(input * mask, mask=negated_mask)


# Metrics below are taken from
# https://github.com/facebookresearch/fastMRI/blob/master/fastmri/evaluate.py
# Copyright (c) Facebook, Inc. and its affiliates.
# Added MAE to the list of metrics


def mae(gt: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """Compute Mean Absolute Error (MAE)"""
    return np.mean(np.abs(gt - pred))


def mse(gt: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """Compute Mean Squared Error (MSE)"""
    return np.mean((gt - pred)**2)


def nmse(gt: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """Compute Normalized Mean Squared Error (NMSE)"""
    return np.linalg.norm(gt - pred)**2 / np.linalg.norm(gt)**2


def psnr(gt: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """Compute Peak Signal to Noise Ratio metric (PSNR)"""
    return peak_signal_noise_ratio(gt, pred, data_range=gt.max())


def ssim(gt: np.ndarray, pred: np.ndarray, maxval: Optional[float] = None) -> np.ndarray:
    """Compute Structural Similarity Index Metric (SSIM)"""
    maxval = gt.max() if maxval is None else maxval

    ssim = 0
    
    for channel in range(gt.shape[0]):
        # Check for either 2D images with multiple channels or 3D images
        # Format is CxHxW or DxHxW
        if gt.ndim == 3:
            ssim = ssim + structural_similarity(gt[channel], pred[channel], data_range=maxval)
        
        # Check for multi-channel 3D images, if so iterate over the channels and depth dims
        # Format is CxDxHxW
        elif gt.ndim == 4:
            for slice_num in range(gt.shape[1]):
                ssim = ssim + structural_similarity(gt[channel, slice_num], pred[channel, slice_num], data_range=maxval)
        else:
            raise NotImplementedError(f"SSIM for {gt.ndim} images not implemented")

    return ssim / gt.shape[0]


METRIC_DICT = {"ssim": ssim, "mse": mse, "nmse": nmse, "psnr": psnr, "mae": mae}


class ValTestMetrics:

    def __init__(self, conf):
        self.conf = conf

    def get_metrics(self, batched_input, batched_target, mask=None):
        batched_input, batched_target = get_npy(batched_input), get_npy(batched_target)
        batched_mask = mask

        metrics = {}

        # Iterating over all metrics that need to be computed
        for metric_name, metric_fn in METRIC_DICT.items():
            if getattr(self.conf[self.conf.mode].metrics, metric_name):
                metric_scores = []

                # If batched mask exists then apply the mask to the inputs and targets
                if batched_mask is not None:
                    batched_input = [create_masked_array(input, mask) \
                                            for input, mask in zip(batched_input, batched_mask)]
                    batched_target = [create_masked_array(target, mask) \
                                            for target, mask in zip(batched_target, batched_mask)]

                # Iterate over input and target batches and compute metrics
                for input, target in zip(batched_input, batched_target):
                    metric_scores.append(metric_fn(target, input))

                # Aggregate metrics over a batch
                metrics[metric_name] = np.mean(metric_scores)

        return metrics

    def get_cycle_metrics(self, batched_input, batched_target):
        batched_input, batched_target = get_npy(batched_input), get_npy(batched_target)

        metrics = {}
        metrics["cycle_SSIM"] = np.mean([ssim(input, target) \
                                    for input, target in zip(batched_input, batched_target)])

        return metrics