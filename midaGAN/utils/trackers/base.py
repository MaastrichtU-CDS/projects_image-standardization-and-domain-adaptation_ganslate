import time
from pathlib import Path

import torchvision
from omegaconf import OmegaConf
from midaGAN.utils import communication, io

from midaGAN.utils.trackers.tensorboard import TensorboardTracker
from midaGAN.utils.trackers.wandb import WandbTracker

class BaseTracker:
    """"Base for training and inference trackers."""

    def __init__(self, conf):
        self.conf = conf
        self.batch_size = self.conf[conf.mode].batch_size
        self.output_dir = self.conf[self.conf.mode].output_dir
        self.iter_idx = None
        self.iter_end_time = None
        self.iter_start_time = None
        self.t_data = None
        self.t_comp = None

        self.wandb, self.tensorboard = self._setup_wandb_tensorboard(conf)
        self._setup_images_dir()
        # TODO: right now it saves it for val too which is unnecessary. Let's see how testing and inferencing will function before changing this
        self._save_config(conf)

    def _save_config(self, conf):
        if communication.get_local_rank() == 0:
            config_path = Path(self.output_dir) / f"{self.conf.mode}/{self.conf.mode}_config.yaml"
            with open(config_path, "w") as file:
                file.write(OmegaConf.to_yaml(conf))

    def _setup_images_dir(self):
        if communication.get_local_rank() == 0:
            io.mkdirs(Path(self.output_dir) / f"{self.conf.mode}/images")

    def _setup_wandb_tensorboard(self, conf):
        wandb, tensorboard = None, None
        if communication.get_local_rank() == 0:
            if conf[conf.mode].logging.wandb:
                wandb = WandbTracker(conf)
            if conf[conf.mode].logging.tensorboard:
                tensorboard = TensorboardTracker(conf)
        return wandb, tensorboard

    def set_iter_idx(self, iter_idx):
        self.iter_idx = iter_idx

    def start_computation_timer(self):
        self.iter_start_time = time.time()

    def start_dataloading_timer(self):
        self.iter_end_time = time.time()

    def end_computation_timer(self):
        self.t_comp = (time.time() - self.iter_start_time) / self.batch_size
        # reduce computational time data point (avg) and send to the process of rank 0
        self.t_comp = communication.reduce(self.t_comp, average=True, all_reduce=False)

    def end_dataloading_timer(self):
        self.t_data = self.iter_start_time - self.iter_end_time
        # reduce data loading per data point (avg) and send to the process of rank 0
        self.t_data = communication.reduce(self.t_data, average=True, all_reduce=False)

    def close(self):
        if communication.get_local_rank() == 0 and self.tensorboard:
            self.tensorboard.close()

    def _save_image(self, visuals, iter_idx):
        name, image = visuals['name'], visuals['image']
        file_path = Path(self.output_dir) / f"{self.conf.mode}/images/{iter_idx}_{name}.png"
        torchvision.utils.save_image(image, file_path)
