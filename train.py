import os
import shutil
import tqdm
import numpy as np
import torch
import torch.optim as optim

from opts import parser
from dataset import AdverbDataset
from model import ActionModifiers, Evaluator

from torch.utils.tensorboard import SummaryWriter

def main(args):
    os.makedirs(args.checkpoint_dir, exist_ok=True)
    save_args(args)

    train_set = AdverbDataset(args.data_dir, args.feature_dir, agg=args.temporal_agg, modality=args.modality,
                                   window_size=args.t, adverb_filter=args.adverb_filter, phase='train')
    train_loader = torch.utils.data.DataLoader(train_set, batch_size=args.batch_size, shuffle=True,
                                              num_workers=args.workers)
    test_set = AdverbDataset(args.data_dir, args.feature_dir, agg=args.temporal_agg, modality=args.modality,
                                  window_size=args.t, adverb_filter=args.adverb_filter, phase='test')
    test_loader = torch.utils.data.DataLoader(test_set, batch_size=args.batch_size, shuffle=False,
                                              num_workers=args.workers)

    model = ActionModifiers(train_set, args).cuda()

    evaluator = Evaluator(train_set, model)

    modifier_params = [param for name, param in model.named_parameters()
                       if ('action_modifiers' in name) and param.requires_grad]
    other_params = [param for name, param in model.named_parameters()
                    if ('action_modifiers' not in name) and param.requires_grad]
    if not args.pretrain_action:
        optim_params = [{'name': 'action_modifiers', 'params': modifier_params},
                        {'name': 'embedding', 'params': other_params}]
    else:
        optim_params = [{'name': 'action_modifiers', 'params': modifier_params, 'lr':0},
                        {'name': 'embedding', 'params': other_params}]
    optimizer = optim.Adam(optim_params, lr=args.lr, weight_decay=args.wd)

    start_epoch = 0
    if args.load is not None:
        checkpoint = torch.load(args.load)
        pretrained_state_dict = checkpoint['net']
        model_state_dict = model.state_dict()
        pretrained_state_dict = {k:v for k, v in pretrained_state_dict.items() if k in model_state_dict}
        model_state_dict.update(pretrained_state_dict)
        model.load_state_dict(model_state_dict)
        start_epoch = checkpoint['epoch']

    writer = SummaryWriter(os.path.join(args.checkpoint_dir, 'log'))

    test(model, test_loader, evaluator, writer, start_epoch)
    for epoch in range(start_epoch, start_epoch+args.max_epochs+1):
        if args.pretrain_action and epoch == args.adverb_start:
            introduce_adverbs(optimizer)
        train(model, train_loader, optimizer, writer, epoch)
        if epoch % args.eval_interval == 0:
            test(model, test_loader, evaluator, writer, epoch)
        if epoch % args.save_interval == 0 and epoch > 0:
            save_checkpoint(model, epoch)
    writer.close()

def save_checkpoint(model, epoch):
    state = {
        'net': model.state_dict(),
        'epoch': epoch,
    }
    torch.save(state, os.path.join(args.checkpoint_dir, 'ckpt_E_%d'%(epoch)))

def introduce_adverbs(optimizer):
    for param_group in optimizer.param_groups:
        if param_group['name'] == 'action_modifiers':
            param_group['lr'] = args.lr * 0.1 
        else:
            param_group['lr'] = args.lr * 0.1


def train(model, train_loader, optimizer, writer, epoch):
    model.train()
    train_loss = 0.0
    act_loss = 0.0
    adv_loss = 0.0
    for idx, data in tqdm.tqdm(enumerate(train_loader), total=len(train_loader)):
        data = [d.cuda() for d in data]
        all_loss = model(data)[0]
        loss = sum(all_loss)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        train_loss += loss.item()
        act_loss += all_loss[0].item()
        adv_loss += all_loss[1].item()
    train_loss /= len(train_loader)
    act_loss /= len(train_loader)
    adv_loss /= len(train_loader)
    writer.add_scalar('Loss/Train/Total', train_loss, epoch)
    writer.add_scalar('Loss/Train/Action', act_loss, epoch)
    writer.add_scalar('Loss/Train/Adverb', adv_loss, epoch)
    print('E: %d | L: %.2E | L_act: %.2E | L_adv: %.2E '%(epoch, train_loss, act_loss, adv_loss))

def test(model, test_loader, evaluator, writer, epoch):
    model.eval()
    accuracies = []
    for idx, data in tqdm.tqdm(enumerate(test_loader), total=len(test_loader)):
        data = [d.cuda() for d in data]
        predictions = model(data)[1]
        adverb_gt, action_gt = data[1], data[2]
        scores, action_gt_scores, antonym_action_gt_scores = evaluator.get_scores(predictions, action_gt, adverb_gt)
        acc = calculate_p1(model.dset, antonym_action_gt_scores, adverb_gt)
        writer.add_scalar('Acc/Test/Video-to-Adverb Antonym', acc, epoch)
        print('E %d | Video-to-Adverb Antonym P@1: %.3f'%(epoch, acc))

def calculate_p1(dset, scores, adverb_gt):
    pair_pred = np.argmax(scores.numpy(), axis=1)
    adverb_pred = [dset.adverb2idx[dset.pairs[pred][0]] for pred in pair_pred]
    acc = (adverb_pred == adverb_gt.cpu().numpy()).mean() ##need way to get pair gt or convert from pair gt to adverb gt
    return acc

def save_args(args):
    shutil.copy('train.py', args.checkpoint_dir)
    #shutil.copy('models.py', args.checkpoint_dir)
    with open(os.path.join(args.checkpoint_dir, 'args.txt'), 'w') as f:
        f.write(str(args))

if __name__ == '__main__':
    args = parser.parse_args()
    if args.modality == 'both':
        args.modality = ['rgb', 'flow']
    else:
        args.modality = [args.modality]
    main(args)
