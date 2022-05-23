import torch.nn.functional as F
import torch
from models.submoduleEDNet import resample2d
import torch.nn as nn

def model_loss_train_self(disp_ests, imgL,imgR,refine_mode,occ_masks,only_train_refine=False):
    if refine_mode:
        if only_train_refine:
            weights = [0.0, 0.0, 0.0,0.5,0.7,1.0]
        else:
            weights = [0.5, 0.7, 1.0, 0.5, 0.7, 1.0]
        scales=[2,1,0,2,1,0]
    else:
        weights = [0.5, 0.7, 1.0]
        scales=[2,1,0]
    all_losses = []
    for disp_est, weight ,scale,occ_mask in zip(disp_ests, weights,scales,occ_masks):
        if weight==0:
            continue
        imgR_cur=F.interpolate(imgR,scale_factor=1/2**scale)
        imgL_cur = F.interpolate(imgL,scale_factor=1/2**scale)
        left_rec = resample2d(imgR_cur, disp_est)

        mean_disp = disp_est.mean(1, True).mean(2, True)
        norm_disp = disp_est / (mean_disp + 1e-7)
        smooth_loss = get_smooth_loss(norm_disp.unsqueeze(1), imgL_cur)
        loss_ph=F.smooth_l1_loss(left_rec[occ_mask],imgL_cur[occ_mask], size_average=True)
        loss_ssim=SSIM(left_rec,imgL_cur)[occ_mask].mean()
        all_losses.append(weight * (0.001*smooth_loss+0.15*loss_ph+0.85*loss_ssim))
        #print("smooth",smooth_loss.item(),"ph",loss_ph.item(),"ssim",loss_ssim.item())
    return sum(all_losses)

def model_loss_train(disp_ests,maxdisp,refine_mode,disp_gt,only_train_refine=False):
    if refine_mode:
        if only_train_refine:
            weights = [0.0, 0.0, 0.0,0.5,0.7,1.0]
        else:
            weights = [0.5, 0.7, 1.0, 0.5, 0.7, 1.0]
        scales=[2,1,0,2,1,0]
    else:
        weights = [0.5, 0.7, 1.0]
        scales=[2,1,0]
    all_losses = []
    for disp_est, weight ,scale in zip(disp_ests, weights,scales):
        if weight==0:
            continue
        gt_scale=F.interpolate(disp_gt.unsqueeze(1),scale_factor=1/2**scale)/2**scale
        gt_scale=torch.squeeze(gt_scale,dim=1)
        mask=(gt_scale<(maxdisp)/2**scale)&(gt_scale>0)
        all_losses.append(weight  * F.smooth_l1_loss(disp_est[mask], gt_scale[mask], size_average=True))
    return sum(all_losses)

def model_loss_test(disp_ests, disp_gt, mask,refine_mode):
    if refine_mode:
        weights = [1.0,1.0]
    else:
        weights=[1.0]
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

def get_smooth_loss(disp, img):
    """Computes the smoothness loss for a disparity image
    The color image is used for edge-aware smoothness
    """
    grad_disp_x = torch.abs(disp[:, :, :, :-1] - disp[:, :, :, 1:])
    grad_disp_y = torch.abs(disp[:, :, :-1, :] - disp[:, :, 1:, :])

    grad_img_x = torch.mean(torch.abs(img[:, :, :, :-1] - img[:, :, :, 1:]), 1, keepdim=True)
    grad_img_y = torch.mean(torch.abs(img[:, :, :-1, :] - img[:, :, 1:, :]), 1, keepdim=True)

    grad_disp_x *= torch.exp(-grad_img_x)
    grad_disp_y *= torch.exp(-grad_img_y)

    return grad_disp_x.mean() + grad_disp_y.mean()

class ContrastiveCorrelationLoss(nn.Module):

    def __init__(self ):
        super(ContrastiveCorrelationLoss, self).__init__()
        self.pointwise=True
        self.zero_clamp=True
        self.use_salience=False
        self.stabalize=False
        self.feature_samples=20
        self.pos_intra_shift=0.18
        self.pos_inter_shift=0.12
        self.neg_samples=10
        self.neg_inter_shift=0.46
    def norm(self,t):
        return F.normalize(t, dim=1, eps=1e-10)

    def sample_nonzero_locations(self,t, target_size):
        nonzeros = torch.nonzero(t)
        coords = torch.zeros(target_size, dtype=nonzeros.dtype, device=nonzeros.device)
        n = target_size[1] * target_size[2]
        for i in range(t.shape[0]):
            selected_nonzeros = nonzeros[nonzeros[:, 0] == i]
            if selected_nonzeros.shape[0] == 0:
                selected_coords = torch.randint(t.shape[1], size=(n, 2), device=nonzeros.device)
            else:
                selected_coords = selected_nonzeros[torch.randint(len(selected_nonzeros), size=(n,)), 1:]
            coords[i, :, :, :] = selected_coords.reshape(target_size[1], target_size[2], 2)
        coords = coords.to(torch.float32) / t.shape[1]
        coords = coords * 2 - 1
        return torch.flip(coords, dims=[-1])

    def tensor_correlation(self,a, b):
        return torch.einsum("nchw,ncij->nhwij", a, b)

    def sample(self,t: torch.Tensor, coords: torch.Tensor):
        if len(t.size())==3:
            return F.grid_sample(t.unsqueeze(1), coords.permute(0, 2, 1, 3), padding_mode='border', align_corners=True)
        else:
            return F.grid_sample(t, coords.permute(0, 2, 1, 3), padding_mode='border', align_corners=True)

    def super_perm(self,size: int, device: torch.device):
        perm = torch.randperm(size, device=device, dtype=torch.long)
        perm[perm == torch.arange(size, device=device)] += 1
        return perm % size

    def standard_scale(self, t):
        t1 = t - t.mean()
        t2 = t1 / t1.std()
        return t2

    def helper(self, f1, f2, c1, c2, shift):
        with torch.no_grad():
            # Comes straight from backbone which is currently frozen. this saves mem.
            fd = self.tensor_correlation(self.norm(f1), self.norm(f2))

            if self.pointwise:
                old_mean = fd.mean()
                fd -= fd.mean([3, 4], keepdim=True)
                fd = fd - fd.mean() + old_mean

        cd = self.tensor_correlation(self.norm(c1), self.norm(c2))

        if self.zero_clamp:
            min_val = 0.0
        else:
            min_val = -9999.0

        if self.stabalize:
            loss = - cd.clamp(min_val, .8) * (fd - shift)
        else:
            loss = - cd.clamp(min_val) * (fd - shift)

        return loss, cd

    def forward(self,
                orig_feats: torch.Tensor, orig_feats_pos: torch.Tensor,
                orig_salience: torch.Tensor, orig_salience_pos: torch.Tensor,
                orig_code: torch.Tensor, orig_code_pos: torch.Tensor,
                ):

        coord_shape = [orig_feats.shape[0], self.feature_samples, self.feature_samples, 2]

        if self.use_salience:
            coords1_nonzero = self.sample_nonzero_locations(orig_salience, coord_shape)
            coords2_nonzero = self.sample_nonzero_locations(orig_salience_pos, coord_shape)
            coords1_reg = torch.rand(coord_shape, device=orig_feats.device) * 2 - 1
            coords2_reg = torch.rand(coord_shape, device=orig_feats.device) * 2 - 1
            mask = (torch.rand(coord_shape[:-1], device=orig_feats.device) > .1).unsqueeze(-1).to(torch.float32)
            coords1 = coords1_nonzero * mask + coords1_reg * (1 - mask)
            coords2 = coords2_nonzero * mask + coords2_reg * (1 - mask)
        else:
            coords1 = torch.rand(coord_shape, device=orig_feats.device) * 2 - 1
            coords2 = torch.rand(coord_shape, device=orig_feats.device) * 2 - 1

        feats = self.sample(orig_feats, coords1)
        code = self.sample(orig_code, coords1)

        feats_pos = self.sample(orig_feats_pos, coords2)
        code_pos = self.sample(orig_code_pos, coords2)

        pos_intra_loss, pos_intra_cd = self.helper(
            feats, feats, code, code, self.pos_intra_shift)
        pos_inter_loss, pos_inter_cd = self.helper(
            feats, feats_pos, code, code_pos, self.pos_inter_shift)

        neg_losses = []
        neg_cds = []
        for i in range(self.neg_samples):
            perm_neg = self.super_perm(orig_feats.shape[0], orig_feats.device)
            feats_neg = self.sample(orig_feats[perm_neg], coords2)
            code_neg = self.sample(orig_code[perm_neg], coords2)
            neg_inter_loss, neg_inter_cd = self.helper(
                feats, feats_neg, code, code_neg, self.neg_inter_shift)
            neg_losses.append(neg_inter_loss)
            neg_cds.append(neg_inter_cd)
        neg_inter_loss = torch.cat(neg_losses, axis=0)
        neg_inter_cd = torch.cat(neg_cds, axis=0)

        return (pos_intra_loss.mean(),
                pos_intra_cd,
                pos_inter_loss.mean(),
                pos_inter_cd,
                neg_inter_loss,
                neg_inter_cd)
