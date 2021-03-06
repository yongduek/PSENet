import os
import cv2
import sys
import time
import collections
import torch
import argparse
import numpy as np
import torch.nn as nn
import torch.nn.functional as F

from torch.autograd import Variable
from torch.utils import data
import shutil
from dataset import IC15TestLoader
import models
import util
# c++ version pse based on opencv 3+
# from pse import pse
# python pse
from pypse import pse as pypse

def extend_3c(img):
    img = img.reshape(img.shape[0], img.shape[1], 1)
    img = np.concatenate((img, img, img), axis=2)
    return img

def debug(idx, img_paths, imgs, output_root):
    if not os.path.exists(output_root):
        os.makedirs(output_root)
    
    col = []
    for i in range(len(imgs)):
        row = []
        for j in range(len(imgs[i])):
            # img = cv2.copyMakeBorder(imgs[i][j], 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=[255, 0, 0])
            row.append(imgs[i][j])
        res = np.concatenate(row, axis=1)
        col.append(res)
    res = np.concatenate(col, axis=0)
    img_name = img_paths[idx].split('/')[-1]
    print(idx, '/', len(img_paths), img_name)
    cv2.imwrite(output_root + img_name, res)

def write_result_as_txt(image_name, bboxes, path):
    filename = util.io.join_path(path, 'res_%s.txt'%(image_name))
    lines = []
    for b_idx, bbox in enumerate(bboxes):
        values = [v for v in bbox]
        line = "%d, %d, %d, %d, %d, %d, %d, %d, %f\n"%tuple(values)
        lines.append(line)
    util.io.write_lines(filename, lines)

def polygon_from_points(points):
    """
    Returns a Polygon object to use with the Polygon2 class from a list of 8 points: x1,y1,x2,y2,x3,y3,x4,y4
    """
    resBoxes=np.empty([1, 8],dtype='int32')
    resBoxes[0, 0] = int(points[0])
    resBoxes[0, 4] = int(points[1])
    resBoxes[0, 1] = int(points[2])
    resBoxes[0, 5] = int(points[3])
    resBoxes[0, 2] = int(points[4])
    resBoxes[0, 6] = int(points[5])
    resBoxes[0, 3] = int(points[6])
    resBoxes[0, 7] = int(points[7])
    pointMat = resBoxes[0].reshape([2, 4]).T
    return plg.Polygon(pointMat)

def test(args):
    data_loader = IC15TestLoader(long_size=args.long_size)
    test_loader = torch.utils.data.DataLoader(
        data_loader,
        batch_size=1,
        shuffle=False,
        num_workers=2,
        drop_last=True)

    submit_path = 'outputs_mlt'
    if os.path.exists(submit_path):
        shutil.rmtree(submit_path)

    os.mkdir(submit_path)

    # Setup Model
    if args.arch == "resnet50":
        model = models.resnet50(pretrained=True, num_classes=7, scale=args.scale)
    elif args.arch == "resnet101":
        model = models.resnet101(pretrained=True, num_classes=7, scale=args.scale)
    elif args.arch == "resnet152":
        model = models.resnet152(pretrained=True, num_classes=7, scale=args.scale)
    
    for param in model.parameters():
        param.requires_grad = False

    model = model.cuda()
    
    if args.resume is not None:                                         
        if os.path.isfile(args.resume):
            print("Loading model and optimizer from checkpoint '{}'".format(args.resume))
            checkpoint = torch.load(args.resume)
            
            # model.load_state_dict(checkpoint['state_dict'])
            d = collections.OrderedDict()
            for key, value in checkpoint['state_dict'].items():
                tmp = key[7:]
                d[tmp] = value
            model.load_state_dict(d)

            print("Loaded checkpoint '{}' (epoch {})"
                  .format(args.resume, checkpoint['epoch']))
            sys.stdout.flush()
        else:
            print("No checkpoint found at '{}'".format(args.resume))
            sys.stdout.flush()

    model.eval()
    
    total_frame = 0.0
    total_time = 0.0
    for idx, (org_img, img) in enumerate(test_loader):
        print('progress: %d / %d'%(idx, len(test_loader)))
        sys.stdout.flush()

        img = Variable(img.cuda(), volatile=True)
        org_img = org_img.numpy().astype('uint8')[0]
        text_box = org_img.copy()

        torch.cuda.synchronize()
        start = time.time()
        # with torch.no_grad():
        #     outputs = model(img)
        #     outputs = torch.sigmoid(outputs)
        #     score = outputs[:, 0, :, :]
        #     outputs = outputs > args.threshold # torch.uint8
        #     text = outputs[:, 0, :, :]
        #     kernels = outputs[:, 0:args.kernel_num, :, :] * text
        # score = score.squeeze(0).cpu().numpy()
        # text = text.squeeze(0).cpu().numpy()
        # kernels = kernels.squeeze(0).cpu().numpy()
        # print(img.shape)
        outputs = model(img)

        score = torch.sigmoid(outputs[:, 0, :, :])
        outputs = (torch.sign(outputs - args.binary_th) + 1) / 2

        text = outputs[:, 0, :, :]
        kernels = outputs[:, 0:args.kernel_num, :, :] * text
        # print(score.shape)
        # score = score.data.cpu().numpy()[0].astype(np.float32)
        # text = text.data.cpu().numpy()[0].astype(np.uint8)
        # kernels = kernels.data.cpu().numpy()[0].astype(np.uint8)
        score = score.squeeze(0).cpu().numpy().astype(np.float32)
        text = text.squeeze(0).cpu().numpy().astype(np.uint8)
        kernels = kernels.squeeze(0).cpu().numpy().astype(np.uint8)
        tmp_marker = kernels[-1, :, :]
            # for i in range(args.kernel_num-2, -1, -1):
            # sure_fg = tmp_marker
            # sure_bg = kernels[i, :, :]
            # watershed_source = cv2.cvtColor(sure_bg, cv2.COLOR_GRAY2BGR)
            # unknown = cv2.subtract(sure_bg,sure_fg)
            # ret, marker = cv2.connectedComponents(sure_fg)
            # label_num = np.max(marker)
            # marker += 1
            # marker[unknown==1] = 0
            # marker = cv2.watershed(watershed_source, marker)
            # marker[marker==-1] = 1
            # marker -= 1
            # tmp_marker = np.asarray(marker, np.uint8)
        sure_fg = kernels[-1, :, :]
        sure_bg = text
        watershed_source = cv2.cvtColor(sure_bg, cv2.COLOR_GRAY2BGR)
        unknown = cv2.subtract(sure_bg,sure_fg)
        ret, marker = cv2.connectedComponents(sure_fg)
        label_num = np.max(marker)
        marker += 1
        marker[unknown==1] = 0
        marker = cv2.watershed(watershed_source, marker)
        marker -= 1
        label = marker
        
        # label = tmp_marker
        # scale = (w / marker.shape[1], h / marker.shape[0])
        scale = (org_img.shape[1] * 1.0 / marker.shape[1], org_img.shape[0] * 1.0 / marker.shape[0])
        bboxes = []
        # print(label_num)
        for i in range(1, label_num+1):
            # get [x,y] pair, points.shape=[n, 2]
            points = np.array(np.where(label == i)).transpose((1, 0))[:, ::-1]
            # similar to pixellink's min_area when post-processing
            if points.shape[0] < args.min_area / (args.scale * args.scale):
                continue
            #this filter op is very important, f-score=68.0(without) vs 69.1(with)
            score_i = np.mean(score[label == i])
            if score_i < args.min_score:
                continue
            rect = cv2.minAreaRect(points)
            bbox = cv2.boxPoints(rect) * scale
            bbox=bbox.reshape(-1).tolist()
            bbox.append(float(score_i))
            # print(score_i)
            # print(float(score_i))
            bboxes.append(np.array(bbox))
            
            # bbox = bbox.astype('int32')

        torch.cuda.synchronize()
        end = time.time()
        total_frame += 1
        total_time += (end - start)
        print('fps: %.2f'%(total_frame / total_time))
        sys.stdout.flush()

        for bbox in bboxes:
            bbox=bbox[:8]
            bbox = bbox.astype('int32')
            cv2.drawContours(text_box, [bbox.reshape(4, 2)], -1, (0, 255, 0), 2)
        


        image_name = data_loader.img_paths[idx].split('/')[-1].split('.')[0]
        write_result_as_txt(image_name, bboxes, 'outputs_mlt/submit_ic15/')
        
        text_box = cv2.resize(text_box, (text.shape[1], text.shape[0]))
        if idx % 200 == 0:
            debug(idx, data_loader.img_paths, [[text_box]], 'outputs_mlt/vis_ic15/')

    cmd = 'cd %s;zip -j %s %s/*'%('./outputs/', 'submit_ic15.zip', 'submit_ic15');
    print(cmd)
    sys.stdout.flush()
    util.cmd.Cmd(cmd)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Hyperparams')
    parser.add_argument('--arch', nargs='?', type=str, default='resnet50')
    parser.add_argument('--resume', nargs='?', type=str, default=None,    
                        help='Path to previous saved model to restart from')
    parser.add_argument('--binary_th', nargs='?', type=float, default=1.0,
                        help='Path to previous saved model to restart from')
    parser.add_argument('--kernel_num', nargs='?', type=int, default=7,
                        help='Path to previous saved model to restart from')
    parser.add_argument('--scale', nargs='?', type=int, default=1,
                        help='Path to previous saved model to restart from')
    parser.add_argument('--long_size', nargs='?', type=int, default=2240,
                        help='Path to previous saved model to restart from')
    parser.add_argument('--min_kernel_area', nargs='?', type=float, default=5.0,
                        help='min kernel area')
    parser.add_argument('--min_area', nargs='?', type=float, default=800.0,
                        help='min area')
    parser.add_argument('--min_score', nargs='?', type=float, default=0.93,
                        help='min score')
    
    args = parser.parse_args()
    test(args)
