"""
Keras implementation of CapsNet in Hinton's paper Dynamic Routing Between Capsules.
The current version maybe only works for TensorFlow backend. Actually it will be straightforward to re-write to TF code.
Adopting to other backends should be easy, but I have not tested this. 

Usage:
       python CapsNet.py
       python CapsNet.py --epochs 100
       python CapsNet.py --epochs 100 --num_routing 3
       ... ...
       
Result:
    Validation accuracy > 99.5% after 20 epochs. Still under-fitting.
    About 110 seconds per epoch on a single GTX1070 GPU card
    
Author: Xifeng Guo, E-mail: `guoxifeng1990@163.com`, Github: `https://github.com/XifengGuo/CapsNet-Keras`
"""

from keras import layers, models, optimizers
from keras import backend as K
from keras.utils import to_categorical
from capsulelayers import CapsuleLayer, PrimaryCap, Length, Mask
from random import randint

K.set_image_data_format('channels_last')


def CapsNet(input_shape, n_class, num_routing):
    """
    A Capsule Network on MNIST.
    :param input_shape: data shape, 3d, [width, height, channels]
    :param n_class: number of classes
    :param num_routing: number of routing iterations
    :return: A Keras Model with 2 inputs and 2 outputs
    """
    x = layers.Input(shape=input_shape)
    y = layers.Input(shape=(n_class,))

    out_list = []
    decoder_y_list = []
    decoder_list = []
    for i in range(args.ensemble):
        # x = Crop()(x)
        idx = randint(0,8)
        idy = randint(0,8)
        layers.Cropping2D(cropping=((idx, idx+24), (idy, idy+24)))(x)

        # Layer 1: Just a conventional Conv2D layer
        conv1 = layers.Conv2D(filters=256, kernel_size=9, strides=1, padding='valid', activation='relu')(x)
        # conv1 = layers.Conv2D(filters=256, kernel_size=9, strides=1, padding='valid', activation='relu', name='conv1')(x)

        # Layer 2: Conv2D layer with `squash` activation, then reshape to [None, num_capsule, dim_vector]
        primarycaps = PrimaryCap(conv1, dim_vector=8, n_channels=32, kernel_size=9, strides=2, padding='valid')

        # Layer 3: Capsule layer. Routing algorithm works here.
        # digitcaps = CapsuleLayer(num_capsule=n_class, dim_vector=16, num_routing=num_routing, name='digitcaps')(primarycaps)
        digitcaps = CapsuleLayer(num_capsule=n_class, dim_vector=16, num_routing=num_routing)(primarycaps)

        # Layer 4: This is an auxiliary layer to replace each capsule with its length. Just to match the true label's shape.
        # If using tensorflow, this will not be necessary. :)
        # out_list.append(Length(name='capsnet')(digitcaps))
        out_list.append(Length()(digitcaps))

        # Decoder network.
        masked_by_y = Mask()([digitcaps, y])  # The true label is used to mask the output of capsule layer. For training
        masked = Mask()(digitcaps)  # Mask using the capsule with maximal length. For prediction

        # Shared Decoder model in training and prediction
        # decoder = models.Sequential(name='decoder')
        decoder = models.Sequential()
        decoder.add(layers.Dense(512, activation='relu', input_dim=16*n_class))
        decoder.add(layers.Dense(1024, activation='relu'))
        decoder.add(layers.Dense(np.prod(input_shape), activation='sigmoid'))
        decoder.add(layers.Reshape(target_shape=input_shape, name='out_recon'))

        decoder_y_list.append(decoder(masked_by_y))
        decoder_list.append(decoder(masked))

    loss_average = layers.Average(name='capsnet')(out_list)

    # loss_sum = layers.Add()(out_list)

    # decoder_y_sum = layers.Add()(decoder_y_list)
    # decoder_sum = layers.Add()(decoder_list)

    # Models for training and evaluation (prediction)
    # train_model = models.Model([x, y], [out_caps, decoder(masked_by_y)])

    # train_model = models.Model([x, y], [loss_average, decoder_y_sum])
    # eval_model = models.Model(x, [loss_average, decoder_sum])

    # train_model = models.Model([x, y], [loss_average]+out_list+decoder_y_list)
    # eval_model = models.Model(x, [loss_average]+out_list+decoder_list)

    train_model = models.Model([x, y], [loss_average]+out_list+decoder_y_list)
    eval_model = models.Model(x, [loss_average]+out_list+decoder_list)

    # train_model = models.Model([x, y], [loss_average, decoder(masked_by_y)])
    # eval_model = models.Model(x, [loss_average, decoder(masked)])

    return train_model, eval_model


def margin_loss(y_true, y_pred):
    """
    Margin loss for Eq.(4). When y_true[i, :] contains not just one `1`, this loss should work too. Not test it.
    :param y_true: [None, n_classes]
    :param y_pred: [None, num_capsule]
    :return: a scalar loss value.
    """
    L = y_true * K.square(K.maximum(0., 0.9 - y_pred)) + \
        0.5 * (1 - y_true) * K.square(K.maximum(0., y_pred - 0.1))

    return K.mean(K.sum(L, 1))


def train(model, data, args):
    """
    Training a CapsuleNet
    :param model: the CapsuleNet model
    :param data: a tuple containing training and testing data, like `((x_train, y_train), (x_test, y_test))`
    :param args: arguments
    :return: The trained model
    """
    # unpacking the data
    (x_train, y_train), (x_test, y_test) = data

    # callbacks
    log = callbacks.CSVLogger(args.save_dir + '/log.csv')
    tb = callbacks.TensorBoard(log_dir=args.save_dir + '/tensorboard-logs',
                               batch_size=args.batch_size, histogram_freq=args.debug)
    checkpoint = callbacks.ModelCheckpoint(args.save_dir + '/weights-{epoch:02d}.h5', monitor='val_capsnet_acc',
                                           save_best_only=True, save_weights_only=True, verbose=1)
    lr_decay = callbacks.LearningRateScheduler(schedule=lambda epoch: args.lr * (0.9 ** epoch))

    # compile the model
    model.compile(optimizer=optimizers.Adam(lr=args.lr),
                  # loss=[margin_loss, 'mse'],
                  loss=[margin_loss]*(args.ensemble+1)+['mse']*args.ensemble,
                  # loss_weights=[2., args.lam_recon],
                  loss_weights=[0.0]+[1.]*args.ensemble+[args.lam_recon]*args.ensemble,
                  metrics={'capsnet': 'accuracy'})

    """
    # Training without data augmentation:
    model.fit([x_train, y_train], [y_train, x_train], batch_size=args.batch_size, epochs=args.epochs,
              validation_data=[[x_test, y_test], [y_test, x_test]], callbacks=[log, tb, checkpoint, lr_decay])
    """

    # Begin: Training with data augmentation ---------------------------------------------------------------------#
    def train_generator(x, y, batch_size, shift_fraction=0.):
        train_datagen = ImageDataGenerator(width_shift_range=shift_fraction,
                                           height_shift_range=shift_fraction)  # shift up to 2 pixel for MNIST
        generator = train_datagen.flow(x, y, batch_size=batch_size)
        while 1:
            x_batch, y_batch = generator.next()
            # yield ([x_batch, y_batch], [y_batch, x_batch])
            yield ([x_batch, y_batch], [y_batch]*(args.ensemble+1)+[x_batch]*(args.ensemble))

    # Training with data augmentation. If shift_fraction=0., also no augmentation.
    model.fit_generator(generator=train_generator(x_train, y_train, args.batch_size, args.shift_fraction),
                        steps_per_epoch=int(y_train.shape[0] / args.batch_size),
                        epochs=args.epochs,
                        validation_data=[[x_test, y_test], [y_test]*(args.ensemble+1)+[x_test]*args.ensemble],
                        callbacks=[log, tb, checkpoint, lr_decay])
    # End: Training with data augmentation -----------------------------------------------------------------------#

    model.save_weights(args.save_dir + '/trained_model.h5')
    print('Trained model saved to \'%s/trained_model.h5\'' % args.save_dir)

    from utils import plot_log
    plot_log(args.save_dir + '/log.csv', show=False)

    return model


def test(model, data):
    x_test, y_test = data
    y_pred, x_recon = model.predict(x_test, batch_size=100)
    print('-'*50)
    print('Test acc:', np.sum(np.argmax(y_pred, 1) == np.argmax(y_test, 1))/y_test.shape[0])

    import matplotlib.pyplot as plt
    from utils import combine_images
    from PIL import Image

    img = combine_images(np.concatenate([x_test[:50],x_recon[:50]]))
    image = img * 255
    Image.fromarray(image.astype(np.uint8)).save("real_and_recon.png")
    print()
    print('Reconstructed images are saved to ./real_and_recon.png')
    print('-'*50)
    plt.imshow(plt.imread("real_and_recon.png", ))
    plt.show()


def load_mnist():
    # the data, shuffled and split between train and test sets
    from keras.datasets import mnist
    (x_train, y_train), (x_test, y_test) = mnist.load_data()

    x_train = x_train.reshape(-1, 28, 28, 1).astype('float32') / 255.
    x_test = x_test.reshape(-1, 28, 28, 1).astype('float32') / 255.
    y_train = to_categorical(y_train.astype('float32'))
    y_test = to_categorical(y_test.astype('float32'))
    return (x_train, y_train), (x_test, y_test)

def load_cifar10():
    # the data, shuffled and split between train and test sets
    from keras.datasets import cifar10
    (x_train, y_train), (x_test, y_test) = cifar10.load_data()
    # x_train = x_train[:1000]
    # y_train = y_train[:1000]

    # print x_train.shape
    # x_train = x_train.dot([0.299, 0.587, 0.114])
    # x_test = x_test.dot([0.299, 0.587, 0.114])

    x_train = x_train.reshape(-1, 32, 32, 3).astype('float32') / 255.
    x_test = x_test.reshape(-1, 32, 32, 3).astype('float32') / 255.
    # y_train = to_categorical(y_train.astype('float32'))
    # y_test = to_categorical(y_test.astype('float32'))

    y_train = to_categorical(y_train.astype('float32'), num_classes=11)
    y_test = to_categorical(y_test.astype('float32'), num_classes=11)

    return (x_train, y_train), (x_test, y_test)

if __name__ == "__main__":
    import numpy as np
    import os
    from keras.preprocessing.image import ImageDataGenerator
    from keras import callbacks
    from keras.utils.vis_utils import plot_model

    # setting the hyper parameters
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch_size', default=100, type=int)
    parser.add_argument('--epochs', default=50, type=int)
    parser.add_argument('--lam_recon', default=1.536, type=float)  # 784 * 0.0005, paper uses sum of SE, here uses MSE
    parser.add_argument('--num_routing', default=3, type=int)  # num_routing should > 0
    parser.add_argument('--shift_fraction', default=0.1, type=float)
    parser.add_argument('--debug', default=0, type=int)  # debug>0 will save weights by TensorBoard
    parser.add_argument('--save_dir', default='./result')
    parser.add_argument('--is_training', default=1, type=int)
    parser.add_argument('--weights', default=None)
    parser.add_argument('--lr', default=0.001, type=float)
    parser.add_argument('--ensemble', default=2, type=int)
    args = parser.parse_args()
    print(args)
    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)

    # load data
    # (x_train, y_train), (x_test, y_test) = load_mnist()
    (x_train, y_train), (x_test, y_test) = load_cifar10()

    # define model
    model, eval_model = CapsNet(input_shape=x_train.shape[1:],
                                n_class=len(np.unique(np.argmax(y_train, 1)))+1,
                                num_routing=args.num_routing)
    model.summary()
    plot_model(model, to_file=args.save_dir+'/model.png', show_shapes=True)

    # train or test
    if args.weights is not None:  # init the model weights with provided one
        model.load_weights(args.weights)
    if args.is_training:
        train(model=model, data=((x_train, y_train), (x_test, y_test)), args=args)
    else:  # as long as weights are given, will run testing
        if args.weights is None:
            print('No weights are provided. Will test using random initialized weights.')
        test(model=eval_model, data=(x_test, y_test))
