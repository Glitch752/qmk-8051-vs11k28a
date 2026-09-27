# i do not know why this works. it was an accident.

def patch(img):
    # replace... some random code (oops) with RET.
    # this proves our fw updates work, at least, i guess
    img[0x7F03] = 0x22
    
    return img