# from __future__ import print_function, division
import argparse
import os
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
from torch.autograd import Variable
import torchvision.utils as vutils
import torch.nn.functional as F
import numpy as np
import time
from tensorboardX import SummaryWriter
from datasets import __datasets__
from models import __models__, model_loss_train, model_loss_test
from models.submoduleEDNet import resample2d
from utils import *
from torch.utils.data import DataLoader
import gc
# from apex import amp
import cv2

cudnn.benchmark = True
os.environ['CUDA_VISIBLE_DEVICES'] = '0'

parser = argparse.ArgumentParser(description='Attention Concatenation Volume for Accurate and Efficient Stereo Matching (ACVNet)')
parser.add_argument('--model', default='acvnet', help='select a model structure', choices=__models__.keys())
parser.add_argument('--maxdisp', type=int, default=192, help='maximum disparity')

parser.add_argument('--dataset', default='sceneflow', help='dataset name', choices=__datasets__.keys())
parser.add_argument('--datapath', default="/data/sceneflow/", help='data path')
parser.add_argument('--trainlist', default='./filenames/train_scene_flow.txt', help='training list')
parser.add_argument('--testlist',default='./filenames/sceneflow_test.txt', help='testing list')
parser.add_argument('--lr', type=float, default=0.001, help='base learning rate')
parser.add_argument('--batch_size', type=int, default=20, help='training batch size')
parser.add_argument('--test_batch_size', type=int, default=16, help='testing batch size')
parser.add_argument('--epochs', type=int, default=50, help='number of epochs to train')
parser.add_argument('--lrepochs',default="20,32,40,44,48:2", type=str,  help='the epochs to decay lr: the downscale rate')

parser.add_argument('--logdir',default='', help='the directory to save logs and checkpoints')
parser.add_argument('--loadckpt', default='./checkpoints/model_sceneflow.ckpt',help='load the weights from a specific checkpoint')
parser.add_argument('--resume', action='store_true', help='continue training the model')
parser.add_argument('--seed', type=int, default=1, metavar='S', help='random seed (default: 1)')
parser.add_argument('--summary_freq', type=int, default=20, help='the frequency of saving summary')
parser.add_argument('--save_freq', type=int, default=1, help='the frequency of saving checkpoint')
parser.add_argument('--refine_mode', action="store_true", help='use refine')
# parse arguments, set seeds
args = parser.parse_args()
torch.manual_seed(args.seed)
torch.cuda.manual_seed(args.seed)
os.makedirs(args.logdir, exist_ok=True)

# create summary logger
print("creating new summary file")
logger = SummaryWriter(args.logdir)

# dataset, dataloader
StereoDataset = __datasets__[args.dataset]
train_dataset = StereoDataset(args.datapath, args.trainlist, True)
test_dataset = StereoDataset(args.datapath, args.testlist, False)
TrainImgLoader = DataLoader(train_dataset, args.batch_size, shuffle=True, num_workers=8, drop_last=True)
TestImgLoader = DataLoader(test_dataset, args.test_batch_size, shuffle=False, num_workers=8, drop_last=False)

# model, optimizer
model = __models__[args.model](maxdisp=args.maxdisp)
model = nn.DataParallel(model)
model.cuda()


# load parameters
start_epoch = 0

if args.loadckpt!='none':
    # load the checkpoint file specified by args.loadckpt
    print("loading model {}".format(args.loadckpt))
    state_dict = torch.load(args.loadckpt)
    model_dict = model.state_dict()
    pre_dict = {k: v for k, v in state_dict['model'].items() if k in model_dict}
    model_dict.update(pre_dict) 
    model.load_state_dict(model_dict)
print("start at epoch {}".format(start_epoch))


def valid():
    epoch_idx=0
    if epoch_idx==0:
        # # testing
        avg_test_scalars = AverageMeterDict()
        for batch_idx, sample in enumerate(TestImgLoader):
            if batch_idx==20:break
            global_step = len(TestImgLoader) * epoch_idx + batch_idx
            start_time = time.time()

            loss, scalar_outputs, image_outputs = test_sample(sample)

            save_scalars(logger, 'test', scalar_outputs, global_step)
            save_images(logger, 'test', image_outputs, global_step)
            avg_test_scalars.update(scalar_outputs)
            print('Epoch {}/{}, Iter {}/{}, test EPE = {:.3f}, time = {:3f}'.format(epoch_idx, args.epochs,
                                                                                     batch_idx,
                                                                    len(TestImgLoader), sum(scalar_outputs["EPE"])/len(scalar_outputs["EPE"]),
                                                                                     time.time() - start_time))
        avg_test_scalars = avg_test_scalars.mean()
        save_scalars(logger, 'fulltest', avg_test_scalars, len(TrainImgLoader) * (epoch_idx + 1))
        print("avg_test_scalars", avg_test_scalars)

        gc.collect()




# test one sample
@make_nograd_func
def test_sample(sample):
    model.eval()
    imgL, imgR, disp_gt = sample['left'], sample['right'], sample['disparity']
    imgL = imgL.cuda()
    imgR = imgR.cuda()
    disp_gt = disp_gt.cuda()
    mask = (disp_gt < args.maxdisp) & (disp_gt > 0)
    disp_ests= model(imgL, imgR,refine_mode=args.refine_mode)
    left_rec = resample2d(imgR, disp_gt)
    left_rec=[left_rec]
    disp_gts = [disp_gt]
    loss = model_loss_test(disp_ests, disp_gt, mask)
    scalar_outputs = {"loss": loss}
    occ_masks = []
    imgL_rev=imgL[:, :, :, torch.arange(imgL.size(3) - 1, -1, -1)]
    imgR_rev=imgR[:, :, :, torch.arange(imgR.size(3) - 1, -1, -1)]
    disp_right = model(imgR_rev, imgL_rev,refine_mode=args.refine_mode)

    disp_right=[i[:,:,torch.arange(i.size(2)-1,-1,-1)] for i in disp_right]

    occ_a=[0.1,0.01]
    for i in range(len(disp_right)):
        disp_rec = resample2d(-disp_right[i], disp_ests[i])
        occ = (torch.abs(disp_rec + disp_ests[i]) > occ_a[i]* (
                    torch.abs(disp_rec) + torch.abs(disp_ests[i])) + 0.5) |( disp_rec == 0)  # from occlusion aware
        occ_masks.append(occ*1.0)
    image_outputs = {"disp_est": disp_ests, "disp_gt": disp_gts, "imgL": imgL, "imgR": imgR,"left_rec":left_rec,"occ_mask":occ_masks,"disp_right":disp_right}
    image_outputs["errormap"] = [disp_error_image_func.apply(disp_est, disp_gt) for disp_est in disp_ests]

    scalar_outputs["EPE"] = [EPE_metric(disp_est, disp_gt, mask) for disp_est in disp_ests]
    scalar_outputs["D1"] = [D1_metric(disp_est, disp_gt, mask) for disp_est in disp_ests]
    scalar_outputs["Thres1"] = [Thres_metric(disp_est, disp_gt, mask, 1.0) for disp_est in disp_ests]
    scalar_outputs["Thres2"] = [Thres_metric(disp_est, disp_gt, mask, 2.0) for disp_est in disp_ests]
    scalar_outputs["Thres3"] = [Thres_metric(disp_est, disp_gt, mask, 3.0) for disp_est in disp_ests]

    return tensor2float(loss), tensor2float(scalar_outputs), image_outputs


if __name__ == '__main__':
    valid()
