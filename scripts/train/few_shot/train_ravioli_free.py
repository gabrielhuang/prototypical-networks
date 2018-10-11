import os
import json
import time

import numpy as np

import torch
import torch.optim as optim
import torch.optim.lr_scheduler as lr_scheduler
import torchvision
import torchnet as tnt

from protonets.engine import Engine

import protonets.utils.data as data_utils
import protonets.utils.model as model_utils
import protonets.utils.log as log_utils


###########################################
# Utils
###########################################
class Summary(object):
    def __init__(self):
        self.logs = {}

    def log(self, epoch, name, value):
        self.logs.setdefault(name, {})
        self.logs[name][epoch] = value

    def sorted(self):
        sorted_logs = {}
        for log in self.logs:
            sorted_logs[log] = self.logs[log].items()
        return sorted_logs

    def print_summary(self, n_avg=50):
        sorted_logs = self.sorted()
        print 'Summary'
        for log in sorted_logs:
            tail = sorted_logs[log]
            tail = tail[-min(len(tail), n_avg):]
            val = dict(tail).values()
            print '\t{}: {:.4f} +/- {:.4f}'.format(log, np.mean(val), np.std(val))

class Timer:
    def __enter__(self):
        self.start = time.clock()
        return self

    def __exit__(self, *args):
        self.end = time.clock()
        self.interval = self.end - self.start

def make_infinite(iterator):
    while True:
        new_epoch = True
        for x in iterator:
            yield x, new_epoch
            new_epoch = False


def main(opt):
    ###########################################
    # Boilerplate
    ###########################################
    if not os.path.isdir(opt['log.exp_dir']):
        os.makedirs(opt['log.exp_dir'])

    # save opts
    with open(os.path.join(opt['log.exp_dir'], 'opt.json'), 'w') as f:
        json.dump(opt, f)
        f.write('\n')

    trace_file = os.path.join(opt['log.exp_dir'], 'trace.txt')

    # Postprocess arguments
    opt['model.x_dim'] = list(map(int, opt['model.x_dim'].split(',')))
    opt['log.fields'] = opt['log.fields'].split(',')

    torch.manual_seed(1234)
    if opt['data.cuda']:
        torch.cuda.manual_seed(1234)

    ###########################################
    # Data
    ###########################################
    if opt['data.trainval']:
        data = data_utils.load(opt, ['trainval'])
        train_loader = data['trainval']
        val_loader = None

        # Prepare datasets
        train_iter = make_infinite(train_loader)
        val_iter = None
    else:
        data = data_utils.load(opt, ['train', 'val'])
        train_loader = data['train']
        val_loader = data['val']

        # Prepare datasets
        train_iter = make_infinite(train_loader)
        val_iter = make_infinite(val_loader)

    ###########################################
    # Create model and optimizer
    ###########################################
    model = model_utils.load(opt)

    if opt['data.cuda']:
        model.cuda()

    Optimizer = getattr(optim, opt['train.optim_method'])
    optimizer = Optimizer(model.parameters(), lr=opt['train.learning_rate'], weight_decay=opt['train.weight_decay'])

    scheduler = lr_scheduler.StepLR(optimizer, opt['train.decay_every'], gamma=0.5)

    ###########################################
    # Training loop
    ###########################################

    summary = Summary()

    #### Start of training loop
    iterations = 1000000
    for iteration in xrange(iterations):

        # Sample from training
        with Timer() as train_load_timer:

            sample, new_epoch = train_iter.next()

        # Compute loss; backprop
        with Timer() as train_backprop_timer:

            optimizer.zero_grad()

            loss, train_info = model.eval_loss(sample)

            loss.backward()
            optimizer.step()

        summary.log(iteration, 'train/acc', train_info['acc'])
        summary.log(iteration, 'train/loss', train_info['loss'])
        summary.log(iteration, 'train/load_time', train_load_timer.interval)
        summary.log(iteration, 'train/bp_time', train_backprop_timer.interval)

        # Sample from validation
        if iteration % 10 == 0 and val_iter is not None:
            with Timer() as val_load_timer:

                sample, __ = val_iter.next()

            with Timer() as val_eval_timer:

                _, val_info = model.eval_loss(sample)

            summary.log(iteration, 'val/acc', val_info['acc'])
            summary.log(iteration, 'val/loss', val_info['loss'])
            summary.log(iteration, 'val/load_time', val_load_timer.interval)
            summary.log(iteration, 'val/eval_time', val_eval_timer.interval)

        # End of epoch? -> schedule new learning rate
        if new_epoch and iteration>0:
            scheduler.step()

        # Save model
        if iteration>0 and new_epoch:
            print 'Saving model'
            model.cpu()
            torch.save(model, os.path.join(opt['log.exp_dir'], 'current_model.pt'))
            if opt['data.cuda']:
                model.cuda()

        # Log

        if iteration % 10 == 0:
            print 'Iteration', iteration
            summary.print_summary()

        #### Save log
        if iteration % 10 == 0:
            with open(os.path.join(opt['log.exp_dir'], 'log.json'), 'wb') as fp:
                json.dump(summary.logs, fp)