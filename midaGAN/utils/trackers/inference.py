#from pathlib import Path
from loguru import logger
import time

import torch

from midaGAN.utils import communication
from midaGAN.utils.trackers.base import BaseTracker
from midaGAN.utils.trackers.utils import (process_visuals_for_logging,
                                          concat_batch_of_visuals_after_gather)


class InferenceTracker(BaseTracker):

    def __init__(self, conf):
        super().__init__(conf)
        self.logger = logger

    def log_iter(self, visuals, len_dataset):
        self._log_message(len_dataset)

        # Gather visuals from different processes to the rank 0 process
        visuals = communication.gather(visuals)
        visuals = concat_batch_of_visuals_after_gather(visuals)
        visuals = process_visuals_for_logging(visuals, single_example=False)

        for i, visuals_grid in enumerate(visuals):
            # In DDP, each process is for a different iter, so incrementing it accordingly
            self._save_image(visuals_grid, self.iter_idx + i)

            if self.wandb:
                self.wandb.log_iter(iter_idx=self.iter_idx + i,
                                    visuals=visuals_grid,
                                    mode=self.conf.mode)

            # TODO: revisit tensorboard support
            if self.tensorboard:
                raise NotImplementedError("Tensorboard tracking not implemented")
                # self.tensorboard.log_iter(self.iter_idx + i,
                #                           learning_rates,
                #                           losses,
                #                           visuals_grid,
                #                           metrics, self.conf.mode)

    def _log_message(self, len_dataset):
        # In case of DDP, if (len_dataset % number of processes != 0),
        # it will show more iters than there actually are
        if self.iter_idx > len_dataset:
            self.iter_idx = len_dataset

        message = f"{self.iter_idx}/{len_dataset} - loading: {self.t_data:.2f}s"
        message += f" | inference: {self.t_comp:.2f}s"
        message += f" | saving: {self.t_save:.2f}s"
        self.logger.info(message)

    def start_saving_timer(self):
        self.saving_start_time = time.time()

    def end_saving_timer(self):
        self.t_save = (time.time() - self.saving_start_time) / self.batch_size
        # Reduce computational time data point (avg) and send to the process of rank 0
        self.t_save = communication.reduce(self.t_save, average=True, all_reduce=False)
