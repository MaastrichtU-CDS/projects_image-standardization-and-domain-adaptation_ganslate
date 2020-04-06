import importlib
import torch.utils.data
#from data.base_data_loader import BaseDataLoader
from data.base_dataset import BaseDataset

def find_dataset_using_name(dataset_name):
    # Given the option --dataset_mode [datasetname],
    # the file "data/datasetname_dataset.py"
    # will be imported.
    dataset_filename = "data." + dataset_name + "_dataset"
    datasetlib = importlib.import_module(dataset_filename)

    # In the file, the class called DatasetNameDataset() will
    # be instantiated. It has to be a subclass of BaseDataset,
    # and it is case-insensitive.
    dataset = None
    target_dataset_name = dataset_name.replace('_', '') + 'dataset'
    for name, cls in datasetlib.__dict__.items():
        if name.lower() == target_dataset_name.lower() \
           and issubclass(cls, BaseDataset):
            dataset = cls
            
    if dataset is None:
        print("In %s.py, there should be a subclass of BaseDataset with class name that matches %s in lowercase." % (dataset_filename, target_dataset_name))
        exit(0)

    return dataset


def get_option_setter(dataset_name):    
    dataset_class = find_dataset_using_name(dataset_name)
    return dataset_class.modify_commandline_options


def create_dataset(opt):
    dataset = find_dataset_using_name(opt.dataset_mode)
    instance = dataset()
    instance.initialize(opt)
    return instance


class CustomDataLoader:
    '''
    Wraps an instance of Dataset class as either a regular Dataloader
    or as a DistributedSampler in case of distributed setup and 
    performs multi-threaded data loading.
    '''
    def name(self):
        return 'CustomDataLoader'

    def __init__(self, opt):
        self.opt = opt
        self.dataset = create_dataset(opt)
        if opt.distributed:
            self.sampler = torch.utils.data.distributed.DistributedSampler(self.dataset, shuffle=not opt.serial_batches)
            self.dataloader = torch.utils.data.DataLoader(
                self.dataset,
                batch_size=opt.batchSize,
                shuffle=False,  # shuffling (or not) is done by DistributedSampler
                sampler=self.sampler,
                num_workers=int(opt.nThreads))

        else:
            self.dataloader = torch.utils.data.DataLoader(
                self.dataset,
                batch_size=opt.batchSize,
                shuffle=not opt.serial_batches,
                num_workers=int(opt.nThreads))

    def __len__(self):
        return min(len(self.dataset), self.opt.max_dataset_size)

    def __iter__(self):
        for i, data in enumerate(self.dataloader):
            if i * self.opt.batchSize >= self.opt.max_dataset_size:
                break
            yield data
