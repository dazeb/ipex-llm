/* ----------------------------------------------------------------------------
 * This file was automatically generated by SWIG (http://www.swig.org).
 * Version 3.0.12
 *
 * Do not make changes to this file unless you know what you are doing--modify
 * the SWIG interface file instead.
 * ----------------------------------------------------------------------------- */

package com.intel.analytics.bigdl.friesian.serving.recall.faiss.swighnswlib;

public class Repeat {
  private transient long swigCPtr;
  protected transient boolean swigCMemOwn;

  protected Repeat(long cPtr, boolean cMemoryOwn) {
    swigCMemOwn = cMemoryOwn;
    swigCPtr = cPtr;
  }

  protected static long getCPtr(Repeat obj) {
    return (obj == null) ? 0 : obj.swigCPtr;
  }

  protected void finalize() {
    delete();
  }

  public synchronized void delete() {
    if (swigCPtr != 0) {
      if (swigCMemOwn) {
        swigCMemOwn = false;
        swigfaissJNI.delete_Repeat(swigCPtr);
      }
      swigCPtr = 0;
    }
  }

  public void setVal(float value) {
    swigfaissJNI.Repeat_val_set(swigCPtr, this, value);
  }

  public float getVal() {
    return swigfaissJNI.Repeat_val_get(swigCPtr, this);
  }

  public void setN(int value) {
    swigfaissJNI.Repeat_n_set(swigCPtr, this, value);
  }

  public int getN() {
    return swigfaissJNI.Repeat_n_get(swigCPtr, this);
  }

  public Repeat() {
    this(swigfaissJNI.new_Repeat(), true);
  }

}