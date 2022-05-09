import torch.nn.functional as F
import torch
from models.submoduleEDNet import resample2d
import torch.nn as nn
def model_loss_train(disp_ests, imgL,imgR):

    weights = [0.7, 0.5, 0.7, 1.0]   #[0.5, 0.5, 0.7, 1.0]
    all_losses = []
    for disp_est, weight in zip(disp_ests, weights):
        left_rec = resample2d(imgR, disp_est)
        all_losses.append(weight * (0.15*F.smooth_l1_loss(left_rec,imgL, size_average=True)+0.85*SSIM(left_rec,imgL).mean(1, True)))
    return sum(all_losses)

def model_loss_train_scale(disp_ests, disp_gt, maxdisp):
    weights = [1, 0.8, 0.8, 0.6,0.3]
    all_losses = []
    scales=[0,1,2,3,3]
    for disp_est, weight ,scale in zip(disp_ests, weights,scales):
        disp_gt_cur=F.avg_pool2d(disp_gt,(2**scale,2**scale))
        mask = (disp_gt_cur < maxdisp) & (disp_gt_cur > 0)
        all_losses.append(weight * F.smooth_l1_loss(disp_est[mask], disp_gt_cur[mask], size_average=True))
    return sum(all_losses)
    
def model_loss_test(disp_ests, disp_gt, mask):
    weights = [1.0] 
    all_losses = []
    for disp_est, weight in zip(disp_ests, weights):
        all_losses.append(weight * F.smooth_l1_loss(disp_est[mask], disp_gt[mask], size_average=True))
    return sum(all_losses)


def SSIM(x,y):
    x=F.pad(x,(1,1,1,1),mode='reflect')
    y = F.pad(y, (1, 1, 1, 1), mode='reflect')
    C1 = 0.01 ** 2
    C2 = 0.03 ** 2

    mu_x = F.avg_pool2d(x,3,1)
    mu_y = F.avg_pool2d(y,3,1)

    sigma_x = F.avg_pool2d(x ** 2,3,1) - mu_x ** 2
    sigma_y = F.avg_pool2d(y ** 2,3,1) - mu_y ** 2
    sigma_xy = F.avg_pool2d(x * y,3,1) - mu_x * mu_y

    SSIM_n = (2 * mu_x * mu_y + C1) * (2 * sigma_xy + C2)
    SSIM_d = (mu_x ** 2 + mu_y ** 2 + C1) * (sigma_x + sigma_y + C2)

    return torch.clamp((1 - SSIM_n / SSIM_d) / 2, 0, 1)