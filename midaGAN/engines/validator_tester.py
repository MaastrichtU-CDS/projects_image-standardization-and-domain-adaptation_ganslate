from midaGAN.engines.base import BaseEngineWithInference
from midaGAN.utils.metrics.val_test_metrics import ValTestMetrics
from midaGAN.utils import environment
from midaGAN.utils.builders import build_gan, build_loader
from midaGAN.utils.io import decollate
from midaGAN.utils.trackers.validation_testing import ValTestTracker


class BaseValTestEngine(BaseEngineWithInference):

    def __init__(self, conf):
        super().__init__(conf)

        self.data_loader = build_loader(self.conf)
        # Val and test modes allow multiple datasets, this handles when it's a single dataset
        if not isinstance(self.data_loader, dict):
            # No name needed when it's a single dataloader
            self.data_loader = {"": self.data_loader}

        self.tracker = ValTestTracker(self.conf)
        self.metricizer = ValTestMetrics(self.conf)

    def run(self, current_idx=""):
        self.logger.info(f'{"Validation" if self.conf.mode == "val" else "Testing"} started.')


        for dataset_name, dataloader in self.data_loader.items():
            self.dataset = dataloader.dataset

            for data in dataloader:
                # Move elements from data that are visuals
                visuals = {
                    "A": data['A'].to(self.model.device),
                    "fake_B": self.infer(data['A']),
                    "B": data['B'].to(self.model.device)
                }

                # Add masks to visuals if they are provided
                if "masks" in data:
                    visuals.update({"masks": data["masks"]})

                metadata = None
                if "metadata" in data:
                    metadata = data["metadata"]

                metrics = self._calculate_metrics(visuals)
                self.tracker.add_sample(visuals, metrics)
                
                self.save_generated_image(visuals["fake_B"], metadata, current_idx, dataset_name)
                

            self.tracker.push_samples(current_idx, prefix=dataset_name)

    def save_generated_image(self, generated_image, metadata=None, idx="", dataset_name=""):
        # A dataset object has to have a `save()` method if it
        # wishes to save the outputs in a particular way or format
        saver = getattr(self.dataset, "save", False)
        if saver:
            save_dir = self.output_dir / f"saved/{dataset_name}/{idx}"
            if metadata is not None:
                saver(generated_image, save_dir, metadata=decollate(metadata))
            else:
                saver(generated_image, save_dir)

    def _calculate_metrics(self, visuals):
        # TODO: Decide if cycle metrics also need to be scaled
        pred, target = visuals["fake_B"], visuals["B"]

        # Check if dataset has `denormalize` method defined,
        denormalize = getattr(self.dataset, "denormalize", False)
        if denormalize:
            pred, target = denormalize(pred), denormalize(target)

        metrics = self.metricizer.get_metrics(pred, target)
        # Update metrics with masked metrics if enabled
        metrics.update(self._get_masked_metrics(pred, target, visuals))
        # Update metrics with cycle metrics if enabled.
        metrics.update(self._get_cycle_metrics(visuals))
        return metrics

    def _get_cycle_metrics(self, visuals):
        """
        Compute cycle metrics from visuals
        """
        cycle_metrics = {}
        if self.conf[self.conf.mode].metrics.cycle_metrics:
            assert 'cycle' in self.model.infer.__code__.co_varnames, \
            "If cycle metrics are enabled, please define behavior of inference"\
            "with a `cycle` flag in the model's `infer()` method"
            rec_A = self.infer(visuals["fake_B"], cycle='B')
            cycle_metrics.update(self.metricizer.get_cycle_metrics(rec_A, visuals["A"]))

        return cycle_metrics

    def _get_masked_metrics(self, pred, target, visuals):
        """
        Compute metrics over masks if they are provided from the dataloader
        """
        mask_metrics = {}

        if "masks" in visuals:
            for label, mask in visuals["masks"].items():
                mask = mask.to(pred.device)
                visuals[label] = 2. * mask - 1
                # Get metrics on masked images
                metrics = {f"{k}_{label}": v \
                    for k,v in self.metricizer.get_metrics(pred, target, mask=mask).items()}
                mask_metrics.update(metrics)
                visuals[label] = 2. * mask - 1

            visuals.pop("masks")

        return mask_metrics


class Validator(BaseValTestEngine):

    def __init__(self, conf, model):
        super().__init__(conf)
        self.model = model

    def _set_mode(self):
        self.conf.mode = 'val'


class Tester(BaseValTestEngine):

    def __init__(self, conf):
        super().__init__(conf)
        environment.setup_logging_with_config(self.conf)
        self.model = build_gan(self.conf)

    def _set_mode(self):
        self.conf.mode = 'test'
