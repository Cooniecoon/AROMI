import cv2
import numpy as np
import socket
import json
from time import time, sleep
import tensorflow as tf
from socket_funcs import *

import os, sys
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))

from tf_pose.estimator import TfPoseEstimator
from tf_pose.networks import get_graph_path, model_wh
from tf_pose.common import CocoPart

from models.classifier.model import import_PoseClassifier,import_FacER

with open('message_code.json', 'r') as f:
    messages = json.load(f)

with open('AWS_IP.txt', 'r') as f:
    TCP_IP = f.readline()

def get_face_crop_img(L_EAR_coordinate,R_EAR_coordinate,x_padding,y_padding,dsize):
    face_box_x=min(L_EAR_coordinate.x, R_EAR_coordinate.x)
    face_box_w=abs(L_EAR_coordinate.x-R_EAR_coordinate.x)
    face_box_y=min(L_EAR_coordinate.y, R_EAR_coordinate.y)
    face_box_h=abs(L_EAR_coordinate.y-R_EAR_coordinate.y)

    x1=int(face_box_x*original_img.shape[1])
    w=int(face_box_w*original_img.shape[1])
    x2=x1+w
    y1=int(face_box_y*original_img.shape[0])

    face_crop=original_img[max(0,y1-int(w)-y_padding):y1+int(w)+y_padding, x1-x_padding:x2+x_padding]
    face_crop_gray=cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    face_box=cv2.resize(face_crop_gray, dsize, interpolation=cv2.INTER_AREA)
    return face_box

def get_pose_vector(human):
    x_data = np.zeros((36), dtype=np.float16)
    for i in range(CocoPart.Background.value * 2):
        if i//2 in human.body_parts.keys():
            body_part = human.body_parts[i//2]
            if i%2==0:
                x_data[i]=body_part.x
            elif i%2==1:
                x_data[i]=body_part.y
    return x_data

def get_human_box(human):
    xmin=2
    ymin=2
    xmax=0
    ymax=0
    for i in range(CocoPart.Background.value):
        if i in human.body_parts.keys():
            body_part = human.body_parts[i]
            vector = np.array([body_part.x, body_part.y], dtype=np.float16)
            xmin=min(xmin,vector[0])
            ymin=min(ymin,vector[1])
            xmax=max(xmax,vector[0])
            ymax=max(ymax,vector[1])
    return [xmin,ymin,xmax,ymax]

def get_area(xyxy):
    h=xyxy[2]-xyxy[0]
    w=xyxy[3]-xyxy[1]
    return w*h

BODY_PARTS={
    'Nose' : 0,
    'REye' : 14, 'LEye' : 15,
    'REar' : 16, 'LEar' : 17,
    'Neck' : 1,
    'RShoulder' : 2, 'RElbow' : 3, 'RWrist' : 4,
    'LShoulder' : 5, 'LElbow' : 6, 'LWrist' : 7,
    'RHip' : 8, 'RKnee' : 9, 'RAnkle' : 10,
    'LHip' : 11, 'LKnee' : 12, 'LAnkle' : 13,
    'Background' : 18
}
emotion_id = {0: "Happy", 1: "Neutral", 2: "Sad"} 
pose_id = {0:"sitting", 1:"standing", 2:"stretching"}
# Pose_classifier_path = "/home/ubuntu/ARoMI/models/classifier/model/pose_classification.weight"
# FacER_model_path="/home/ubuntu/ARoMI/models/classifier/model/FacER.weight"
Pose_classifier_path = "../models/classifier/model/pose_classification.weight"
FacER_model_path="../models/classifier/model/FacER.weight"
# FacER_model_path="C:\\Users\\jeongseokoon\\projects\\ARoMI\\models\\classifier\\model\\MobileNet_V2_4.weight"

if __name__ == "__main__":
    fourcc = cv2.VideoWriter_fourcc(*'DIVX')
    out_person = cv2.VideoWriter('out_person_eyecontact.avi', fourcc, 15.0, (432, 368))
    out_people = cv2.VideoWriter('eyecontact.avi', fourcc, 13.0, (432, 368))

    Pose_classifier = import_PoseClassifier(output_shape=len(pose_id))
    Pose_classifier.load_weights(Pose_classifier_path)

    print('\n\nPose_classifier Loaded')
    FacER_model=import_FacER()
    FacER_model.load_weights(FacER_model_path)
    print('\n\nFacER Loaded')
    w, h = model_wh("432x368") # default=0x0, Recommends : 432x368 or 656x368 or 1312x736 "
    e = TfPoseEstimator(
        get_graph_path("mobilenet_v2_large"), # "mobilenet_thin", "mobilenet_v2_large", "mobilenet_v2_small"
        target_size=(w, h),
        trt_bool=False,
    )
    
    print('\n\nconnect server')

    # 연결할 서버(수신단)의 ip주소와 port번호 : eyes
    TCP_PORT_eyes = 1111
    sock_eyes = socket.socket()
    sock_eyes.connect((TCP_IP, TCP_PORT_eyes))

    sleep(1)
    # 연결할 서버(수신단)의 ip주소와 port번호 : image
    TCP_PORT_img = 2222
    sock_img = socket.socket()
    sock_img.connect((TCP_IP, TCP_PORT_img))

    sleep(1)
    # 연결할 서버(수신단)의 ip주소와 port번호 : classifier
    TCP_PORT_classifier = 4444
    sock_classifier = socket.socket()
    sock_classifier.connect((TCP_IP, TCP_PORT_classifier))

    # sleep(1)
    # # 연결할 서버(수신단)의 ip주소와 port번호 : pose
    # TCP_PORT_pose = 5555
    # sock_pose = socket.socket()
    # sock_pose.connect((TCP_IP, TCP_PORT_pose))

    time_0=time()

    # default setting
    nose_x=0.5
    emotion='Neutral'
    emotion_idx=1
    pose='sitting'
    pose_idx=0

    THRESHOLD_TIME=3 #seconds
    print('\n\nstart\n\n')
    while True:
        
        t=time()
        image = recv_img_from(sock_img)

        original_img = image.copy()

        humans = e.inference(
            image,
            resize_to_default=(w > 0 and h > 0),
            upsample_size=4.0,
        )
        # if human detected
        if len(humans)>0:
            
            # Filtering Only Big Person            
            human_norm=0
            for human in humans:
                human_box=get_human_box(human)
                human_size=get_area(human_box)
                if human_size>human_norm:
                    human_norm=human_size
                    User=human
            Body_Parts=User.body_parts

            x_data = get_pose_vector(User)

            # print(x_data.reshape(1,x_data.shape[0], x_data.shape[1],1))
            preds = Pose_classifier.predict(
                        np.reshape(x_data,(1,36))
                    ) #! pose classification
            pose_idx = int(np.argmax(preds))
            
            pose = pose_id[pose_idx]#! pose

            # pose=list(class_id.keys())[np.argmax(preds[0])] 

            cv2.putText(image, text=pose, org=(30, 30), 
                        fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=0.7, 
                        color=(255, 255, 0), thickness=2)

            L_EAR_CHECK=BODY_PARTS['LEar'] in Body_Parts.keys()
            R_EAR_CHECK=BODY_PARTS['REar'] in Body_Parts.keys()
            NOSE_CHECK=BODY_PARTS['Nose'] in Body_Parts.keys()

            if NOSE_CHECK:
                nose_x=Body_Parts[BODY_PARTS['Nose']].x
                print(nose_x)
            
            if (L_EAR_CHECK and R_EAR_CHECK and NOSE_CHECK):
                dt=time()-time_0

                cv2.putText(image, text="eye contacted, time : {0:.2f} sec".format(dt), org=(30, 70), 
                        fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=0.7, 
                        color=(0, 0, 0), thickness=2)

                # get face image
                L_EAR_coordinate=Body_Parts[BODY_PARTS['LEar']]
                R_EAR_coordinate=Body_Parts[BODY_PARTS['REar']]

                face_box=get_face_crop_img(
                    L_EAR_coordinate, R_EAR_coordinate,
                    x_padding=15, y_padding=10,
                    dsize=(48,48)
                    )

                # face_box=tf.image.grayscale_to_rgb(face_box, name=None)
                input_face_box=np.expand_dims(np.expand_dims(face_box, -1), 0)

                prediction = FacER_model.predict(input_face_box)
                emotion_idx = int(np.argmax(prediction))
                emotion = emotion_id[emotion_idx] #! Face Emotion

                # face_box_forView=cv2.resize(face_box, (128,128), interpolation=cv2.INTER_AREA)

                # cv2.putText(face_box_forView, text=emotion, org=(15, 15), 
                #         fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=0.7, 
                #         color=(0, 255, 0), thickness=2)
                # cv2.imshow("Face Box", face_box_forView) #! face_box : input of FER

                if dt > THRESHOLD_TIME:
                    time_0=time()

                    # print('Eye Contact') #! Chatbot Litsening
                    sock_eyes.send(messages['chatbot'].encode())
                    
                

            elif ((not L_EAR_CHECK or not R_EAR_CHECK) and NOSE_CHECK):
                # sock_eyes.send(messages['pass'].encode())
                # print('Look Elsewhere')
                # print('chatbot off')
                time_0=time()
                pass

            image_single = TfPoseEstimator.draw_humans(image, [User], imgcopy=True)
            out_person.write(image_single)
            # cv2.imshow("tf-pose-estimation result Filtered", image_single)

        sock_eyes.send(messages['pass'].encode())
        image_all = TfPoseEstimator.draw_humans(image, humans, imgcopy=True)

        # send_image_to(image_single,sock_img,dsize=(200, 100))
        msg = "{0:0<6}".format(round(nose_x,4))
        msg =msg+','+str(pose_idx)+','+str(emotion_idx)
        print('msg :',msg, '  len :',len(msg))
        send_message_to(msg,sock_classifier)
        
        out_people.write(image)

        # send_image_to(face_box_forView,sock_eyes,dsize=(face_box_forView.shape[1], face_box_forView.shape[0]))
        # cv2.imshow("tf-pose-estimation result All", image_all)

        fps=1/(time()-t)

        print(f'fps : {fps:.2f}')
        print('message : ',msg)
        print('face emotion :',emotion, '  idx :',emotion_idx)
        print('user pose :',pose,'  idx :',pose_idx,end='\n\n')
        # if cv2.waitKey(1) == 27:
        #     break

    cv2.destroyAllWindows()